# AI assist

Optional help from Claude while you test. It is **off by default**: open **AI** in the top bar and turn on **AI assist** (saved as `aiAssistEnabled` in `.env`). It needs an Anthropic API key: add `ANTHROPIC_API_KEY=...` to `.env` (or sign in with `ant auth login`). The model is `claude-opus-5-5`; set `AUTOMATENY_AI_MODEL` to change it.

Claude only **suggests**. Nothing it returns is saved or run until you click, and every call and every accepted suggestion is written to `reports/ai-audit.jsonl` in the workspace.

## What it does

| Where | Action | What happens |
|---|---|---|
| History / Control → a failed test's details | **Explain this failure** | Classifies the cause (locator changed, timing, real bug, environment, test data, test definition) with evidence and next steps. |
| Same dialog, when a page field is to blame | **Suggest a locator** | Up to 3 replacement locators, each **counted against the failure's DOM snapshot** (no site is visited). **Apply to <page>** shows the change and saves the page only when you confirm. |
| Generator → Pages → scan button on a field | **Suggest a locator with AI** (after a check that found 0 or several matches) | Suggestions are checked **on the live page** first; **Use in editor** puts one in the field, unsaved, so you review and Save. |
| Generator → Test cases → ✨ | **Draft a test case** | Describe the flow in plain English; Claude may only use this system's pages and fields. The draft is validated and shown as JSON; **Create test case** opens the usual file dialog. |
| History → run details | **AI summary** | A short plain-English summary of the run for people who don't read test logs, with a copy button. |

## What is sent, and what is not

Sent only when you click an AI action:

- triage: the error, traceback, the end of the test log, text attachments (e.g. the failed action), the failure screenshot and DOM snapshot;
- locator suggestions: the field's config and the page HTML (scripts, styles and password values removed);
- drafts: your description and the names and types of the system's page fields, plus one existing test case as an example;
- summaries: test names, outcomes and the first line of each error. No logs.

Before anything is sent, the values of credential keys in `.env` and the environment (anything that looks like a user name, password, token or key) are replaced with `[REDACTED:<KEY>]`, and token-shaped strings (API keys, JWTs, bearer tokens, `password=...`, webhook URLs) are masked.

Text from the tested site is marked as untrusted data: Claude is told it is data to analyze, never instructions to follow. Still, treat suggestions as you would a colleague's, and check them; locator suggestions show their match counts so you can.
