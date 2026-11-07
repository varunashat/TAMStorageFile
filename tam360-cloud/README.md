# TAM 360 · cloud Azure Calculator agent

Your Azure AI Foundry model (gpt-6.1-sol) builds estimates in the **official Azure Pricing Calculator** and returns the **share link**, fully online.

## How it works
- **Azure Container App** (UAE North, 1 always-on replica) running Chromium on a virtual display, the agent (`calculator_agent.py`), and a small web page.
- **Microsoft Entra sign-in** in front of everything: only your organisation's users can open it.
- **One-time calculator sign-in:** "Open live browser view" shows the cloud browser (noVNC) in your own browser. Sign in there with the **dedicated calculator Microsoft account** yourself. Only the sign-in cookies are kept, on Azure Files (`session.json`); passwords are never stored.
- **AI:** Foundry is called through the app's **managed identity** (role *Cognitive Services OpenAI User*). There are no keys, and tokens bill to your Foundry.
- **Safety:** the model can't leave Microsoft sites, can't click Log in, Save, Share, Delete or purchase controls (the app saves and shares itself), and never types prices. Page text is treated as untrusted data. Every step is logged.

## Deploy (Azure Cloud Shell, Bash): one command
```bash
git clone -b tam360-cloud https://github.com/varunashat/TAMStorageFile && cd TAMStorageFile/tam360-cloud && bash deploy.sh
```
The script creates resource group `rg-tam360-calc` with a registry, storage, identity and Container Apps environment. It builds the image **in Azure** (no Docker needed), grants the identity access to your Foundry resource, deploys the app and turns on Entra sign-in. At the end it prints the URL.
Your account needs: Contributor (or Owner) on the subscription/resource group, permission to assign roles on the Foundry resource (Owner or User Access Administrator), and permission to create an app registration. If app registration is blocked, the script disables the public address and tells you what to ask IT (`auth.sh`).

Change defaults with environment variables, e.g. `RG=my-rg LOC=uaenorth FOUNDRY_DEPLOYMENT=gpt-6.1-sol bash deploy.sh`.
Update after code changes: run `bash deploy.sh` again (a new image tag is built and deployed).

## Use
1. Open the URL and sign in with your Noventiq account.
2. **Step 1:** "Sign in to the calculator account" → "Open live browser view" → sign in with the dedicated account (password, MFA, "Stay signed in: Yes"). One time only.
3. **Step 2:** type the services, e.g. *UAE North: Azure Firewall Standard, DDoS Protection, Application Gateway WAF v2*, then click **Build & get share link**.
4. The share link appears with Copy and Open buttons. "What the agent did" shows every step.

Link from TAM 360: `https://<app-url>/?request=<url-encoded services>&name=<estimate name>` opens the page pre-filled.

## Cost (approx.)
Container Apps 1 vCPU / 2 GiB with one always-on replica (idle rate when not working) is roughly USD 15–40/month, plus registry Basic about USD 5, storage under USD 1 and Log Analytics a few USD. Foundry tokens bill as usual (about 5–8k tokens per calculator step).
