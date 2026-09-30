# Findings: which method catches which posting error

This is the argument behind the numbers in the [README](../README.md). The data is synthetic: a generator seeds eight types of error into 113,981 journal entries. Every figure below is measured on the test split, the last 6 of 24 fiscal periods: 28,695 entries, of which 1,293 contain a seeded error. Sources are `notebooks/05_evaluation.ipynb` and the validation scripts.

## In short

1. **Which method works depends on the error type, not on how sophisticated the model is.** Three SQL statements catch three error types that the best model almost entirely misses.
2. **Labels are worth about 3× in average precision**, but the labelled models may be learning how the generator seeded each error, not the error itself. So blind and labelled methods are reported separately.
3. **Combining models that read the same features didn't help.** Adding detectors that read *different data* did.
4. **The threshold comes from a cost model** and is reported as a band (0.45–0.60), because the cost curve is flat across it.
5. **Backdated entries are the real gap:** recall 0.260 against a measured ceiling of 0.838.
6. **Three problems were found and fixed along the way.** Each one taught something about how checks fail.

## 1. Method choice follows error structure

Each error type leaves a different kind of trace. The detector that works is the one that reads that trace:

| Error type | What gives it away | Caught first by | Final recall |
|---|---|---|---:|
| Unbalanced | Debits ≠ credits within one entry | L1: SQL balance check (`GROUP BY … HAVING`) | 1.000 |
| Duplicate | The same entry posted again within days | L2: SQL self-join on employee, accounts and amount | 1.000 |
| Unmatched bank | A ledger cash line with no bank counterpart | L3: reconciliation blocking join | 0.988 |
| Round number | Suspiciously round totals | L4: XGBoost | 1.000 |
| Unusual account pair | A rarely used combination of accounts | L4: XGBoost | 0.893 |
| Structuring | Amounts split to stay under a limit | L4: XGBoost | 0.847 |
| Off-hours posting | Posted outside normal hours | L4: XGBoost | 0.792 |
| Backdated | Transaction date well before posting date | L4: XGBoost | 0.260 |

The supervised models could not learn the first three types, because the feature table has no feature for them: nothing measures the size of an imbalance, how close an entry is to a duplicate, or whether it reconciles.
- Random forest and XGBoost reach only **0.02–0.11** recall on those three types.
- Split by whether the feature table covers a type, the models score **0.705–0.833** recall on the five covered types and **0.041–0.226** on the three that aren't.

The gap comes from what the features cover, not from how strong the model is.

Adding the three SQL layers to XGBoost (both at threshold 0.50) raised recall from **0.621 to 0.835**, and precision rose too, from **0.440 to 0.502**. Precision rose because almost all of the 326 extra flags were real errors. Per error type, the layers added +0.957 recall on duplicates, +0.927 on unmatched bank items and +0.908 on unbalanced entries, and about zero on the other five types.

The layers barely overlap: only **17 of the 1,710** entries the final system flags are caught by more than one layer. Each layer contributes its own catches instead of competing with the others. In the final configuration (XGBoost at 0.57), the three SQL layers add 333 flags, 284 of them real errors, for about 0.6 extra analyst-hours a month.

## 2. Blind methods vs labelled models: two scoreboards

- **Scoreboard A (no labels): the realistic estimate.**
  - The best F1 is segmented IQR: precision 0.248, recall 0.295, F1 0.269.
  - Segmented z-score is the precision specialist: 0.464 precision at 0.111 recall.
  - The blind methods mostly detect unusual *amounts*. IQR catches 59.9% of round-number errors and 77.7% of structuring, but under 7% of anything else.
  - Recall on the medium-difficulty tier falls to 0.04–0.08 for every blind method.
- **Scoreboard B (trained on labels): an upper bound.**
  - Logistic regression: precision 0.172, recall 0.695.
  - Random forest: 0.748 / 0.554.
  - XGBoost: 0.440 / 0.621, with average precision (AP) 0.638.

Why keep them apart? The labels record *how the generator created each error*, not real fraud. Where the seeding writes a distinctive value (a total forced to a round figure, an hour rewritten to 00:00–05:00), a model can learn a rule that is perfectly predictive here and meaningless on a real ledger. Ranking both kinds of method in one table would make the labelled models look better than they would be in practice.

**Benford's Law is a lesson in matching the test to the unit.** At the entry level it's no better than random: precision 0.058 against a 4.5% base rate, AP 0.049. Benford is a test on an *account's* whole digit distribution, so pushing an account's result down to its entries flags them all equally. Its account-level ranking still picks out the deviating accounts correctly.

**High recall has to be read next to the flag rate.** Logistic regression "wins" several error types, but it flags 18.2% of the ledger and produces 4,312 false positives. A recall figure means little without the review cost it comes with.

## 3. Combining methods: a negative result

- A soft-vote ensemble of the three models reached AP **0.6373**, against **0.6375** for XGBoost alone. Rank-averaging scored 0.6123.
- The union of all blind rules raised recall to 0.504, but F1 fell to 0.208 (the best single blind method has 0.269).
- The union of XGBoost with the blind rules reached recall 0.749 at F1 0.274 (XGBoost alone: 0.515).

Ensembling models that read the same 23 features adds more opinions, not more information. The layered system works for the opposite reason: each layer reads different inputs.

## 4. Reconciliation: the problem was scoring, not matching candidates

Matching the ledger to a deliberately messy bank feed works in two steps. First, SQL finds candidate bank rows by amount and a 0–3 day date window; this step is called blocking. Then `rapidfuzz` compares the reference text of each candidate.

- A journal-entry number recovered from the bank reference resolved **53.9%** of bank rows outright.
- String matching at threshold 70 resolved a further **12.6%**, leaving **32.9%** unmatched.

To find out why the rest failed, the candidate search was re-run with 100× the amount tolerance and a 5× wider date window. **None** of the unmatched rows had a better candidate under the wider search. So the loss came from scoring the candidates, not from finding them. Checked later against labels, this held up: flagging "no candidate within one cent and 0–3 days" scores **precision 1.000 and recall 0.988** on the ledger side (0.987 on the bank side), without using any label.

This number depends on the generator: bank amounts mirror the ledger exactly, and the ledger reference field has no counterparty text for fuzzy matching to use.

## 5. Choosing the threshold

The threshold is chosen by expected review cost:
- A false positive costs 5 analyst-minutes to review.
- A missed error costs 480 minutes × a 20% chance it escalates = 96 minutes.
- That makes a miss about **19:1** against a false alarm, and the cheapest threshold is **0.57**.

At 0.57, XGBoost alone reaches recall 0.610 and precision 0.573, costs about 8.2 analyst-hours a month, and flags 4.8% of entries, inside a 5% review budget.

Two alternatives show why the cost model needs both of its parts:
- **Without the 20% escalation factor (96:1),** the optimum drops to 0.10 and flags 91% of the ledger. Any linear cost model does this without an escalation probability and a capacity limit.
- **The F1-optimal threshold (0.79)** implicitly prices a missed error at about 31 analyst-minutes, an assumption nobody made on purpose.

**The threshold is a band, not a constant.** Fixing one boolean feature moved the cost-optimal threshold from 0.46 to 0.57, while average precision moved by 0.001. The cost curve is nearly flat between about 0.45 and 0.60. So 0.57 is reported as one point inside an operating band, to be reviewed against the workload the team actually sees.

## 6. Backdated entries: the gap, and measuring its ceiling honestly

The generator deliberately makes **17.5%** of backdated entries undetectable. They have none of the three risk drivers (manual source, after-hours posting, period-end posting), and their posting lag sits in the normal 0–2 day range. That caps recall on the hard tier at **0.838**. The best any method reached on that tier is **0.346** (logistic regression), and the final system catches 0.260 of backdated entries overall. That gap is a detection gap, not a design limit.

Measuring the ceiling correctly needed a fix first. The feature table's period-end flag covered the last 2 *calendar* days of a period, while the generator selects on the last 3 *business* days. Measured through the mismatched flag, the undetectable share came out at about 42%, which would have understated the ceiling at about 0.58. After aligning the feature, the measurement matched the 17.5% the generator targets. The fix moved detection by less than a point; it mattered for measuring honestly, not for detection.

Two features could close the gap:
- the posting lag compared with **each poster's own history**, instead of one global threshold;
- an **interaction** across the three risk drivers, instead of three independent flags.

## 7. What went wrong, and how it was caught

**A data leak.** The structuring routine appended `" (split n/m)"` to the entry description. The models read this through `description_length`, which ranked #3 in SHAP importance, and structuring, a "hard" error type, scored 1.000 recall in all three models.
- *What gave it away:* perfect recall on a hard type, with a feature that has no business predicting it.
- *The fix:* remove the suffix and regenerate the data with the same seed.
- *The result:* structuring recall fell to 0.864 / 0.682 / 0.868 (LR / RF / XGBoost), and the top of the SHAP ranking became a three-way contest between behavioural, structural and amount features.

The leak had also distorted an earlier conclusion: it made the EDA hypothesis that behavioural signal outranks structural look confirmed. After the fix, that hypothesis is only partly confirmed.

**A period-end flag that measured the wrong thing** (section 6). *What gave it away:* the measured undetectable share (~42%) didn't match the generator's known 17.5%.

**A double count in a stored table.** Cross-checking the Power BI measures against SQL showed the stored per-type scoreboard reporting reconciliation recall of **1.037**, which is impossible (85 caught out of 82).
- *The cause:* a pandas index lookup counted entries with two error labels twice.
- *Why nothing caught it:* the notebook's round-trip check compared the overall rows and one per-type query, but never read back the per-slice rows. **A round-trip check only covers the rows it actually reads.**
- *The fix:* the check now rebuilds every per-slice row in SQL.

No figure reported in the notebook was affected, but a dashboard reading the table would have been.

All three were found by checking a number against something independent: the generator's design, a different calculation, or a second tool. The validation now in place follows the same idea:
- **Power BI:** 21 of the 24 DAX measures are compared directly with SQL. The other three are built from checked measures or are a guard.
- **Streamlit:** 842 checks of `metrics.py` against the same SQL, 0 mismatches. Deliberately injected faults were caught.

## 8. Limits of these findings

- **The data is synthetic.** These are method comparisons under known conditions, not performance figures to expect on a real ledger.
- **One seed, one split, no confidence intervals.** Differences of a point or two between methods aren't meaningful; the argument above rests on order-of-magnitude differences.
- **Posting volume is concentrated.** 39 employees post entries, and the top 15 make 88.6% of them, so the behavioural features partly describe a few people's habits.
- **The unsupervised methods were fitted on the whole ledger, test period included.** That's standard for unsupervised detection, but it means Scoreboard A isn't strictly out-of-sample.

The full list, with the evidence for each item, is in Section 8 of `notebooks/05_evaluation.ipynb`.
