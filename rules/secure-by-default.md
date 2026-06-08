---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

Generate code that's secure by default — because the model's default is not. Validate external input, never hardcode secrets, parameterize queries, keep insecure conveniences out, and verify any package you import actually exists. Treat security as part of "working," not a later pass.

The numbers justify the paranoia: ~45% of AI-generated code fails security tests (Veracode), spanning 38 CWE categories (8 in the 2023 Top-25), and the rate doesn't improve with smarter models — it's systemic.

## The forms

**1. Missing input validation** — the single most common flaw. Any value from outside (request body, query param, file, env, third-party response) is untrusted until validated.
```python
# BAD — trusts the path straight from the request
open(f"/data/{request.args['file']}")          # path traversal: ?file=../../etc/passwd
# GOOD — validate/normalize and confine
name = secure_name(request.args["file"])        # reject separators; resolve under a fixed root
```

**2. Injection** — never build SQL / shell / HTML / queries by string concatenation of input.
```python
# BAD
db.execute(f"SELECT * FROM users WHERE id = {uid}")
os.system("convert " + filename)
# GOOD — parameterize / pass args as a list / escape on output
db.execute("SELECT * FROM users WHERE id = %s", [uid])
subprocess.run(["convert", filename])
```

**3. Hardcoded secrets** — keys, tokens, passwords, connection strings in source (≈30% of vulnerabilities in some models). Read from env/secret store. Don't log secrets either. Don't put real-looking secrets in "examples" — juniors copy them to prod.

**4. Insecure defaults** — `debug=True` in prod, `CORS(*)`, `verify=False` on TLS, permissive file modes, auth disabled "for now." The safe setting is the default; loosen only with a stated reason.

**5. Weak/misused crypto** — no MD5/SHA1 for passwords, no homemade crypto, no ECB mode, no hardcoded IVs. Use the platform's vetted high-level primitives (e.g. a maintained password-hashing/`AEAD` library).

**6. Missing authz/authn** — don't assume the caller is allowed. Check permission at the operation, not just at the route.

**7. Hallucinated / typosquatted packages** — models invent package names (21.7% of OSS recommendations); attackers register them ("slopsquatting"). Before adding a dependency, confirm it exists, is the one you mean, and is maintained — don't import on faith. And don't add a dependency for something trivial (`reuse-before-reinvent.md`).

## How to apply

- For each external input on the path you're touching: where is it validated? If nowhere, that's the bug.
- Run the project's security/SAST and dependency-audit tooling if it exists; treat findings as blockers.
- A `# nosec`/suppression needs a named, concrete justification — never a reflex to quiet the scanner (same discipline as `concrete-types.md`'s `type: ignore`).

## Why this rule exists

Models reproduce vulnerable patterns straight from their training corpus without modeling the threat — and the insecure version usually looks identical to the secure one, so it sails through review and static analysis. The fix is cheap at write-time and expensive after a breach. Default to the secure construction every time; make insecurity the thing that requires a justification.

(Aside, harness-level: a malicious rule/instruction file can carry a hidden "rules-file backdoor." Read rule files you didn't write before trusting them.)
