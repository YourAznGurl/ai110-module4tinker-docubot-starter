"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Splitting documents into small snippets (paragraphs)
- Building a simple retrieval index
- Retrieving relevant snippets (with a refusal guardrail)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini
"""

import os
import re
import glob

# Common words that don't help us find the right doc, so we ignore them.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "do", "does", "did",
    "how", "what", "where", "which", "who", "when", "why", "can", "i", "me",
    "my", "we", "you", "your", "to", "of", "in", "on", "at", "for", "and",
    "or", "it", "its", "this", "that", "there", "any", "all", "with", "from",
    "about", "as", "by", "if", "should", "could", "would", "get",
    "these", "those", "mention", "doc", "docs", "documentation",
}

# Guardrail: a snippet must match at least this many different query words
# (for queries with 2+ meaningful words) or DocuBot refuses to answer.
MIN_MATCHES = 2


class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory: list of (filename, text)
        self.documents = self.load_documents()

        # Split every document into small snippets: list of (filename, snippet)
        self.chunks = self.split_into_chunks(self.documents)

        # Count how many snippets contain each word (used to rank rare words higher)
        self.chunk_counts = self.count_words_in_chunks(self.chunks)

        # Build the inverted index: word -> list of filenames
        self.index = self.build_index(self.documents)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    # -----------------------------------------------------------
    # Helpers: snippets and words
    # -----------------------------------------------------------

    def split_into_chunks(self, documents):
        """
        Splits each document into paragraphs (blocks separated by a blank line).
        A heading line (starting with #) is glued onto the paragraph after it,
        so every snippet keeps its title for context.
        Returns a list of (filename, snippet_text).
        """
        chunks = []
        for filename, text in documents:
            pending_heading = ""  # remembers a heading until we see its paragraph
            for paragraph in text.split("\n\n"):
                paragraph = paragraph.strip()
                if not paragraph:
                    continue  # skip empty blocks
                # A one-line block starting with "#" is a heading
                if paragraph.startswith("#") and "\n" not in paragraph:
                    pending_heading = paragraph
                    continue
                if pending_heading:
                    paragraph = pending_heading + "\n" + paragraph
                    pending_heading = ""
                chunks.append((filename, paragraph))
            # A heading with nothing after it still becomes its own snippet
            if pending_heading:
                chunks.append((filename, pending_heading))
        return chunks

    def stem(self, word):
        """
        Cuts common endings off a word so similar words match each other.
        Example: connect, connection, connecting -> "connect"
                 generate, generated, generates -> "generat"
        We only cut one ending, and only if 3+ letters remain.
        """
        for suffix in ("ions", "ion", "ing", "ed", "es", "s", "e"):
            if word.endswith(suffix) and len(word) - len(suffix) >= 3:
                # Don't cut the "s" from words like "access" or "process"
                if suffix == "s" and word.endswith("ss"):
                    continue
                return word[:-len(suffix)]
        return word

    def tokenize(self, text):
        """
        Turns text into a list of clean lowercase words.
        - re.findall pulls out only letters and numbers (drops punctuation,
          and splits things like auth_token into "auth" and "token")
        - stopwords like "the" and "how" are removed
        - stem() shortens each word so similar words match
        """
        words = re.findall(r"[a-z0-9]+", text.lower())
        cleaned = []
        for word in words:
            if word in STOPWORDS:
                continue
            cleaned.append(self.stem(word))
        return cleaned

    def count_words_in_chunks(self, chunks):
        """
        Returns a dictionary: word -> how many snippets contain that word.
        A word found in few snippets is "rare", so it is more useful.
        """
        counts = {}
        for filename, chunk in chunks:
            for word in set(self.tokenize(chunk)):
                counts[word] = counts.get(word, 0) + 1
        return counts

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def build_index(self, documents):
        """
        Builds a tiny inverted index mapping lowercase words to the
        documents they appear in.

        Example:
        {
            "token": ["AUTH.md", "API_REFERENCE.md"],
            "databas": ["DATABASE.md"]
        }
        """
        index = {}
        for filename, text in documents:
            # set() so each word is counted once per document
            for word in set(self.tokenize(text)):
                if word not in index:
                    index[word] = []
                index[word].append(filename)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query, text):
        """
        Returns a relevance score. Each query word found in the text adds
        points, and rare words add more than common ones.
        Higher means more relevant.
        """
        query_words = set(self.tokenize(query))
        text_words = set(self.tokenize(text))
        score = 0.0
        # "&" gives the words that appear in both sets
        for word in query_words & text_words:
            # 1 divided by "number of snippets with this word" = rare words win
            score += 1.0 / self.chunk_counts.get(word, 1)
        return score

    def retrieve(self, query, top_k=3):
        """
        Returns up to top_k relevant snippets as (filename, snippet_text),
        sorted by score (best first).
        Returns an empty list when nothing is relevant enough (guardrail).
        """
        query_words = set(self.tokenize(query))
        if not query_words:
            return []  # nothing meaningful in the question

        # Step 1: use the index to find which files mention any query word
        candidate_files = set()
        for word in query_words:
            for filename in self.index.get(word, []):
                candidate_files.add(filename)

        # Guardrail rule: how many query words must a snippet match?
        if len(query_words) >= 2:
            required_matches = MIN_MATCHES
        else:
            required_matches = 1

        # Step 2: score every snippet inside the candidate files
        scored = []
        for filename, chunk in self.chunks:
            if filename not in candidate_files:
                continue
            chunk_words = set(self.tokenize(chunk))
            matches = len(query_words & chunk_words)  # different words matched
            if matches >= required_matches:  # guardrail: drop weak matches
                score = self.score_document(query, chunk)
                scored.append((score, filename, chunk))

        # Step 3: best score first (ties keep their original order)
        scored.sort(key=lambda item: item[0], reverse=True)

        # Step 4: keep the top_k and drop the scores
        results = []
        for score, filename, chunk in scored[:top_k]:
            results.append((filename, chunk))
        return results

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        RAG mode.
        Uses our retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        # Guardrail: no evidence means we never even call the LLM
        if not snippets:
            return "I do not know based on these docs."

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        Used for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)