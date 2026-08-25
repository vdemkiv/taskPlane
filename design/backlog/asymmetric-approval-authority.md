# Design backlog — asymmetric approval authority

## Disposition

Shelved by accepted human decision 0008, “Adopt attributed trust-source authority and shelf asymmetric work.” This element is conditional architecture inventory only. It is not an approved current-delivery contract, Plan task, implementation/evaluation target, release claim, or new R-0002 debt record.

Accepted decision 0009 registers the inherited limitation: human approval records an unauthenticated actor string and current shelf signing uses a symmetric private-runtime secret. The R-0002 `--trust-source` mode does not solve or strengthen that standing state.

## Pickup triggers

Re-open this Design only when at least one is true:

1. a second operator must independently rely on the approval evidence;
2. the producer host is not trusted by the consumer; or
3. evidence must be verified outside the originating Taskplane/operator environment.

## Preferred conditional architecture

Use the host OpenSSH runtime, specifically `ssh-keygen -Y sign` at separately authorized producer boundaries and `ssh-keygen -Y verify` at the consumer. Commit a closed allowed-signers trust file containing public identities only. Bind signatures to exact producer role, source SHA, canonical Design fingerprint, and closed approval/engine claim bytes with a fixed namespace.

The future consumer fails closed when OpenSSH is absent, too old for `-Y`, returns a nonzero result, or cannot parse the fixed allowed-signers file. No fallback to structural acceptance, HMAC, a different executable, or a hand-rolled verifier is allowed.

This direction adds no Python signing dependency and no hand-rolled cryptography. It does require a new human scope decision that proves OpenSSH availability across supported producer and consumer hosts, authorizes exact human and engine signing producers, names their private-key loading boundaries outside Git, and permits the minimum protected-surface integration. Private keys, private-key encodings, and signing secrets never enter the repository or pickup consumer.

## Rotation and migration inventory

A later authorized source SHA may commit a replacement allowed-signers entry and newly produced evidence. Revocation/rotation is bounded to explicit repository revisions; there is no online CA or retroactive mutation of historical receipts. Existing symmetric evidence cannot be silently relabeled as asymmetric evidence and requires explicit reissuance under the future contract.

## Explicit exclusions from current R-0002 delivery

- no OpenSSH probe or subprocess;
- no allowed-signers file;
- no signer/verifier/key module or graph edge;
- no public/private key material;
- no dependency or packaging change;
- no Plan task, acceptance/evaluation claim, release note, or 2.17.20 implementation claim; and
- no `tp req debt` record.
