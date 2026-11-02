# Learnt: Key Engineering Takeaways

- **Conway's Law**: Architecture mirrors organization communication; microservices solve team coordination bottlenecks, not code speed.
- **Modular Monolith**: One codebase, one process, separated by folders; instantaneous in-memory calls eliminate network and distributed systems overhead for solo devs.
- **Polyglot Persistence vs. Postgres**: Enterprises juggle DynamoDB/Redis/Elasticsearch; solo devs can run 99% of workloads on PostgreSQL with relational tables + `JSONB` + `pgvector`.
- **JSONB & GIN Index**: Stores unstructured/changing receipt data safely inside SQL while `GIN` (`jsonb_path_ops`) keeps key-path queries lightning fast.
- **winget**: Windows Package Manager CLI that downloads official installers, verifies SHA-256 hashes, and installs software silently without browser ads.
- **git vs. gh**: `git` tracks files and commit history locally on your machine; `gh` is an API client that talks to GitHub's remote web platform (PRs, issues, repos).
- **RAG & Vector DBs (Scale Threshold)**: Markdown files suffice up to hundreds of concepts; beyond that, avoid heavy Pinecone clusters—use `pgvector` inside your existing Postgres or embedded LanceDB for zero-infra semantic retrieval.
- **Software Licenses (Permissive vs. Copyleft)**: Permissive (MIT/Apache 2.0) allows closed commercial reuse with attribution; Copyleft (GPL/AGPL) mandates that all derivative works remain free and open source.
