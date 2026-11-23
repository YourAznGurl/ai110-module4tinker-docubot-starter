# DocuBot Model Card

## 1. System Overview

**What is DocuBot trying to do?**
DocuBot answers developer questions about a project using only the local files in the docs/ folder. It compares three approaches: a naive LLM, retrieval only, and retrieval plus an LLM (RAG), to show how retrieval keeps answers grounded in real documentation.

**What inputs does DocuBot take?**
A user question typed into the command line, the .md and .txt files in docs/, and an API key stored in a .env file (needed only for modes 1 and 3).

**What outputs does DocuBot produce?**
Mode 1: a free-form LLM answer. Mode 2: raw snippets labeled with filenames. Mode 3: a short LLM answer built only from retrieved snippets that names the files used, or "I do not know based on these docs."

---

## 2. Retrieval Design

**How does your retrieval system work?**
- Indexing: each doc is turned into lowercase words. Punctuation is removed (so auth_token becomes "auth" and "token"), common stopwords are dropped, and a simple stemming function trims endings so "connect", "connection", and "connecting" match. An inverted index maps each word to the files that contain it.
- Snippets: each doc is split into paragraphs (separated by blank lines), and each heading is attached to the paragraph below it.
- Scoring: each query word found in a snippet adds points, and rare words (found in few snippets) add more than common ones.
- Selection: the index finds candidate files, every snippet in those files is scored, and the top 3 are returned.

**What tradeoffs did you make?**
I chose simplicity over accuracy. Word matching is fast and easy to understand, but it does not understand meaning or synonyms. Paragraph snippets are more precise than whole files, but a paragraph can miss context from nearby paragraphs. Rare-word weighting improved ranking, but it can still push a loosely related snippet into the top 3.

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**
- Naive LLM mode: always calls Gemini. The starter prompt only contains the question, so the model never sees the docs.
- Retrieval only mode: never calls the LLM.
- RAG mode: calls Gemini only after retrieval finds relevant snippets. If retrieval finds nothing, DocuBot refuses without calling the LLM.

Note: the starter's model name (gemini-flash-lite-latest) returned a 404 error, so I changed the model in llm_client.py to gemini-3.5-flash-lite.

**What instructions do you give the LLM to keep it grounded?**
The prompt tells Gemini to use only the provided snippets, not to invent functions, endpoints, or settings, to reply "I do not know based on the docs I have." when the snippets are not enough, and to mention which files it used.

---

## 4. Experiments and Comparisons

| Query | Naive LLM: helpful or harmful? | Retrieval only: helpful or harmful? | RAG: helpful or harmful? | Notes |
| --- | --- | --- | --- | --- |
| Where is the auth token generated? | Harmful: no answer, asked for project details and never used our docs | Helpful: returned AUTH.md snippets (evaluation.py confirmed the right file) | Not recorded | Naive mode refused to guess but gave no value |
| How do I connect to the database? | Harmful: confident answer with invented DB_HOST, DB_PORT, DB_USER settings and a guess to check config/ | Helpful: returned DATABASE.md snippets | Helpful: grounded summary (db.py helpers, SQLite locally, PostgreSQL in production), cites DATABASE.md | Naive looked helpful but was not from our docs |
| Which endpoint lists all users? | Harmful: guessed GET /users | Helpful: returned API_REFERENCE.md snippets | Mostly helpful: correct GET /api/users, cites API_REFERENCE.md | Docs say the endpoint is admin-only; RAG did not mention that |
| How does a client refresh an access token? | Not tested | Helpful but noisy: found POST /api/refresh and the AUTH.md workflow step, plus a loosely related intro paragraph | Helpful: short correct answer (call /api/refresh), cites API_REFERENCE.md and AUTH.md | |
| Is there any mention of payment processing? | Not tested | Helpful: refused with "I do not know" | Helpful: refused with "I do not know" without calling the LLM | Guardrail works |

**What patterns did you notice?**
- Naive LLM looks impressive on the database question because the answer is detailed and specific, but it is made up and not tied to our docs. Even when it hedged, it guessed a URL that differs from the real one.
- Retrieval only is clearly better when I need the exact source text, but the raw paragraphs are harder to read and can include a weak extra snippet.
- RAG is clearly better than both when the answer is in the docs: it is short, readable, grounded, and cites files.

---

## 5. Failure Cases and Guardrails

**Describe at least two concrete failure cases you observed.**

Failure case 1: "Which endpoint lists all users?" in naive mode. The model guessed GET /users. The docs say GET /api/users and that it is only accessible to admins. It should have said it could not know without the docs.

Failure case 2: "How does a client refresh an access token?" in retrieval only mode. The third result was the introduction paragraph of AUTH.md, which only matched general words like token and client. It should have returned only the two relevant snippets.

Failure case 3: "Which endpoint lists all users?" in RAG mode. The answer was correct but left out the docs' note that the endpoint is admin-only, which matters for safe use. RAG can still drop important details when it summarizes.

**When should DocuBot say "I do not know based on the docs I have"?**
- When the question is about something that is not in the docs (like payment processing).
- When the retrieved snippets only match one generic word and do not actually answer the question.

**What guardrails did you implement?**
- A snippet must match at least 2 different query words (when the question has 2 or more meaningful words), otherwise it is dropped.
- If no snippets remain, retrieval only mode and RAG mode both return "I do not know," and RAG does not call the LLM.
- The RAG prompt requires using only the snippets, refusing when evidence is missing, and naming the files used.

---

## 6. Limitations and Future Improvements

**Current limitations**
1. Keyword matching does not understand synonyms or meaning, so a question worded very differently from the docs may be refused.
2. Paragraph snippets can split an explanation across several snippets or include loosely related text.
3. The refusal threshold is a fixed number, so it can wrongly refuse or wrongly answer on edge cases.

**Future improvements**
1. Use embeddings (semantic search) instead of word matching.
2. Improve ranking, for example by boosting matches in headings and filtering out low-scoring extras.
3. Have the RAG prompt quote the exact snippet it used and keep important warnings (like "admin only") in the answer.

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**
A wrong or invented answer could lead a developer to call the wrong endpoint or misconfigure a database or security setting. Over-trusting fluent LLM output is risky, especially in naive mode. RAG can also omit important details such as access restrictions, and missing or outdated docs lead to missing or outdated answers.

**What instructions would you give real developers who want to use DocuBot safely?**
- Always check the cited file before acting on an answer.
- Do not use naive LLM mode for real decisions.
- Keep the docs up to date, because DocuBot is only as accurate as them.
- Treat "I do not know" as a signal to ask a teammate or improve the docs.