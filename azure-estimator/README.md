# Azure Advanced Estimator

Priced, proposal-ready Azure BOQ from a BOQ template, an RVTools export, an Azure Migrate assessment or JSON line items.
Prices come only from the Microsoft Azure Retail Prices API: pay-as-you-go, 1/3-year reserved, 1/3-year savings plan,
Azure Hybrid Benefit and a recommended mix. Outputs: Excel BOQ (Summary, BOQ, Assumptions, Funding, Price sources), HTML, JSON,
and VM rows for the Azure Pricing Calculator automation.

```
pip install openpyxl
python azure_estimator.py template BOQ-template.xlsx
python azure_estimator.py estimate input.xlsx --customer "Contoso" --currency AED --region uaenorth --out out/contoso
```
Used by the Cloud Command Center v2.0 dashboard (TAM 360 Copilot) and the `azure-boq-estimator` Claude skill.
