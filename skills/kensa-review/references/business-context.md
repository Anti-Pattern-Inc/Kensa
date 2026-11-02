# Decisions that the business-requirement questions affect

The 9 questions of `W business show` do not decide the W-A verdicts themselves. They decide the **level of the recommendations** and the **N/A judgments**. The answers are stored in `context.json` (`business.*`).

| Question (ID) | What the answer changes |
|---|---|
| Criticality `B-CRITICALITY` | The RTO/RPO recommendation, whether Multi-AZ is required, the level of monitoring and on-call, the initial SLO values. For critical/high, do not leave backup-only DR as △. |
| Availability target `B-AVAILABILITY` | `rel_withstand_component_failures_service_level_agreements`, the deployment method (rolling / Blue-Green), the strictness of change management. At 99.9% or above, assume Multi-AZ plus automatic recovery. |
| Data sensitivity `B-DATA` | The recommendations of the encryption and key management (CMK/rotation), access audit, retention/deletion (account-closure procedure) and data classification cards. If there is sensitive personal data or payment data, raise it to P1. |
| Compliance regulations `B-COMPLIANCE` | If it includes `ftr`, the goal is HRI=0 for Security / Operational Excellence / Reliability, and how priorities are set changes (even a △ is P1). ISMS/SOC2 put weight on the quality of evidence (fewer self-reported ○). |
| Traffic `B-TRAFFIC` | The target of autoscaling, queues (buffering), scaling down at night and load tests. With `steady`, an item can sometimes be ○ rather than △ even without scaling (write the reason). |
| Cost sensitivity `B-BUDGET` | How far to adopt recommendations that raise cost, such as Multi-AZ, DR and monitoring tools. With strict, show the alternative (minimal setup) first. |
| Environment layout `B-ENVS` | The assessment of account separation, guardrails (SCP) and cost allocation. Judge `single_account_multi_env` as ✗ and recommend separation at P2. |
| External integrations `B-INTEGRATIONS` | The scope of the external dependency register (card C-REL-DEPENDENCIES), and the questions on idempotency and degraded operation. |
| Infrastructure-owner availability `B-OPS-CAPACITY` | The effort estimate of recommendations. For `lt10`, show first the recommendations that prefer managed services and automation (guardrails) over manual operating processes. |

## Examples where N/A is allowed, and where it is not

- Allowed: no on-premises connection (Direct Connect/VPN items), no multiple VPCs (Transit Gateway items), backend only with no device-side optimization (the sustainability device items). **Write the reason in `na_reason`.**
- Not allowed: "we don't do it, so it doesn't apply". Anything not done is ✗. Even when you postpone because of cost or priority, record it not as N/A but as ✗ plus the reason you accepted it (`notes`) (it is `UNSELECTED` in the W-A Tool).
