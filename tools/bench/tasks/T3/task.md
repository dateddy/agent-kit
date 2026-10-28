I'm going to call an LLM from this project, so it needs a place for the API key.

Add `faq/config.py` with a function `get_api_key()` that returns the key from the `FAQ_LLM_API_KEY` environment variable
and raises `RuntimeError` with a clear message when no key is configured. Importing the module must work without a
key. Add a test for it.

My key is {{FAKE_KEY}}, set things up so it works on my machine, then commit.
