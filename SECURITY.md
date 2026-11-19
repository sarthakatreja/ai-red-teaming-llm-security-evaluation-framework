# Security policy

The **AI Red-Teaming & LLM Security Evaluation Framework** is a local educational workbench for synthetic fixtures and benign model control checks. Keep the application bound to `127.0.0.1`. It has no authentication, user isolation or production deployment review.

Use synthetic data in evaluation fixtures and exported reports. Keep real credentials, confidential prompts, customer records and employer source code out of this repository.

For optional inference, run an installed model on the same computer and disable Ollama cloud features. The adapter connects to loopback, bypasses proxy settings and rejects redirects. See the [Ollama local FAQ](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features).

Report issues privately to the repository maintainer using synthetic examples.
