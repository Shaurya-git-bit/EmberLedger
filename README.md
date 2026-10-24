# EmberLedger

Cookstove carbon credits that are paid only for days a stove was really used, enforced by a smart contract.

**Track:** Carbon Markets & Emissions Transparency
**Hackathon:** ClimateChain Global Hackathon 2026
**Demo video:** [add link]

## What this is

EmberLedger turns temperature readings from a clean cookstove into verified usage days. A smart contract then issues one credit per verified day and releases payment to households. The AI finds the cooking. The blockchain decides what counts.

## The problem

Clean cookstoves earn carbon credits, but most credits are issued on assumed usage. A stove that was given out is treated as a stove that is used. Reviews of the cookstove market have questioned how many credits are backed by real use. Buyers cannot check it, and honest projects lose trust along with weak ones.

Sensors exist, but sensor data alone does not fix this. The data sits in a database controlled by the project operator. Whoever controls the database can edit readings, add missing days, claim a day twice, or reuse an old cooking record.

## The solution

1. Each stove's sensor signs its daily data.
2. An ML model finds cooking events in the temperature readings.
3. A sanity check rejects events that are too cool, too short or too smooth to be real cooking, and a duplicate check rejects copied traces.
4. The ProofOfUse smart contract checks the sensor's signature, links the day to the previous record, and issues one credit.
5. Payment is held in escrow for a waiting period, then split between the households.

## Why blockchain is essential

Remove the contract and the system falls apart:

- **The AI alone can be fooled.** In our demo, a fake heating curve passes the ML check. Only the contract stops it, because the edited data no longer matches what the sensor signed.
- **A database can be rewritten.** Each day is hash-linked to the one before it, so past days cannot be changed or backfilled without breaking the chain.
- **No one party can be trusted to enforce the rules.** The operator, the buyer and the auditor all need rules that none of them can change. The contract enforces one credit per day, a cap of 2 cooking events per day, and no reuse of an event.
- **Payment follows proof.** Escrow, the waiting period and the auditor's deposit penalty run on-chain, so households are paid without a middleman and false claims cost the operator money.

The contract rejects fraud with these messages: `bad sensor signature`, `chain broken`, `day already issued`, `duplicate event`, `event cap`.

## Results

**Fraud protection**
- 6 of 6 contract attack tests blocked: claim a day twice, replay an old event, use a stale chain head, claim 3 events in one day, sign with a fake key, edit data after signing.
- The live tamper demo shows a fake trace fooling the AI and being rejected by the contract.

**ML detection (held-out households, never used in training)**

| Stove | PR-AUC | F1 |
|---|---|---|
| SUM_18 | 0.885 | 0.878 |
| SUM_21 | 0.919 | 0.844 |

- All 10 true usage days were caught. 12 stove-days were verified, and 10 of those match the rule-based usage labels.
- Out-of-fold PR-AUC on training households: 0.934.

**Payments**
- 0.001 ETH per verified day, split across the household. SUM_18 received 0.009 ETH and SUM_21 received 0.003 ETH after the waiting period.

**Climate number**
- 3.77 kg CO2e baseline per household-day.
- The 12 verified days cover about 45 kg CO2e (34 to 56 kg across the fNRB uncertainty).
- Crediting every logged day would claim 120.6 kg, which is 167% above what was verified. EmberLedger keeps that gap out of the market.

## Run the demo

```
pip install -r requirements.txt
streamlit run proof.py
```

The first run downloads the Solidity 0.8.20 compiler, so it needs internet. The app has four tabs: Smart contract, Check a day, Fraud attempt, and Payments and impact. Use **Reset chain** in the sidebar to start clean.

## Repo structure

| File | What it does |
|---|---|
| `proof.py` | Streamlit demo app |
| `pipeline.py` | Data loading, ML cooking detection, sanity and duplicate checks, fake-trace generator |
| `chain.py` | Compiles and deploys the contract on a local test chain, then signs, submits, pays and challenges |
| `app_data/` | Files the app needs: `ProofOfUse.sol`, `model.joblib`, `library.pkl`, `params.json`, `verified_days.json`, `summary.json`, `attacks.json`, `SUM_18.csv`, `SUM_21.csv` |
| `test-ieee (1).ipynb` | End-to-end notebook: data screening, ML, SHAP, fraud tests, contract tests, climate number |
| `requirements.txt` | Python packages |

The notebook was run on Kaggle, so its paths point to `/kaggle/input/...`. Change them to your local data folder to rerun it.

## Data

Stove-use data from the Stove Use Monitoring System repository (Rwanda, Phase 2, 2 to 17 Oct 2019). Six Phase 2 files with clean clocks are used: SUM_7, SUM_9, SUM_11 and SUM_17 for training, and SUM_18 and SUM_21 as held-out test households (12,541 rows in total). Raw files are available from Zenodo (reference S4).

## Current scope and next steps

- The sensor keys are simulated and the demo replays real sensor data. Real hardware would hold the key.
- The contract runs on a local test chain. The next step is a public testnet deployment.
- Usage labels come from a rule, so ML scores measure agreement with that rule. Real usage surveys would add an independent check.
- Thermocouple readings alone cannot separate every heat source. A second sensor is a planned addition, and the contract's signature and chain checks already stop edited or copied data.
- Tested on 2 held-out households. More households would strengthen the numbers.
- The climate figure is an illustrative application of the Gold Standard baseline, not net credits.

## Climate impact: sources and tests

Method: SMEC V4.0 default baseline (Option B), applied per household-day. This is an illustrative application. Eligibility of a refugee-camp deployment is not assessed.

| Parameter | Value | Source |
|---|---|---|
| Wood use per person | 0.50 t/yr | S1, parameter SMEC 12 |
| Discount | 0.95 | S1, Eq. 3 |
| NCV | 0.0156 TJ/t | S1, parameter SMEC 10 |
| EF CO2 | 112 tCO2/TJ | S1, parameter SMEC 8 |
| EF non-CO2 | 9.46 tCO2e/TJ | S1, parameter SMEC 9 |
| fNRB | 0.33 (Rwanda, national) | S2, Table 3 |
| Household size | 4 persons | S3 |

Checks: totals reproduced by hand (3.77 kg x 12 = 45.2 kg). Both held-out households have 16 logged days with no empty days and no gap over 12 hours. Parameters were read directly from S1 and S2.

Not applied, so the figure is an upper bound on baseline covered and not net credits: downward adjustment factor, activity emissions, 2% market leakage, embodied emissions and the Hawthorne adjustment. The baseline stove type (clay stoves in Kigeme camp) and camp-level fNRB and household size are still to be confirmed.

### References

- S1: Gold Standard for the Global Goals, *Simplified Methodology for Clean and Efficient Cookstoves (SMEC)*, V4.0, GS4GG PAA M400-07, published 05/05/2026. https://globalgoals.goldstandard.org/standards/408_V4.0_PAA-M400-07_SMEC.pdf
- S2: UNFCCC Article 6.4 Mechanism, *Methodological tool: Fraction of non-renewable biomass*, A6.4-AMT-009, V01.0, in force 21 May 2026. https://unfccc.int/sites/default/files/resource/A6.4-AMT-009-v01.0_public.pdf
- S3: The New Times (Rwanda), "Rwanda's population passes 13 million", 27 Feb 2023 (2022 census). https://www.newtimes.co.rw/article/5371/news/rwanda/rwandas-population-passes-13-million
- S4: Gaura, Bhargava, Verba, Brusey (Coventry University), *EPSRC HEED Data Repository: Stove Use Monitoring System*, Zenodo, v1.0, 15 Jul 2020. https://zenodo.org/record/3947000

All sources accessed 24 Oct 2026.
