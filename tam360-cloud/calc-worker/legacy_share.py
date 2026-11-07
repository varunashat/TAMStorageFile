"""Local Edge calculator runner. Model extracts intake; deterministic code controls browser.

Only VM compute is supported in this first adapter. Unsupported services fail closed.
No Azure provisioning, role assignment, credential storage or portal automation.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import html
import json
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

ROOT = Path(__file__).resolve().parent
CALCULATOR = 'https://azure.microsoft.com/en-us/pricing/calculator/'
EXCLUSIONS = ['Managed disks', 'Storage transactions', 'Outbound data transfer',
              'Backup', 'Monitoring', 'Taxes', 'Customer agreement discounts']


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')


def validate(data):
    if not isinstance(data, dict):
        raise ValueError('Workload must be a JSON object.')
    if set(data) != {'name', 'currency', 'items', 'exclusions', 'questions'}:
        raise ValueError('Expected name, currency, items, exclusions and questions only.')
    if not isinstance(data['name'], str) or not 1 <= len(data['name']) <= 120:
        raise ValueError('Name must contain 1 to 120 characters.')
    if data['currency'] != 'USD':
        raise ValueError('This verified adapter supports USD only.')
    if not isinstance(data['questions'], list) or data['questions']:
        raise ValueError('Resolve all intake questions before running.')
    if not isinstance(data['exclusions'], list) or not all(isinstance(x, str) for x in data['exclusions']):
        raise ValueError('Exclusions must be a list of text values.')
    if not set(EXCLUSIONS).issubset(data['exclusions']):
        raise ValueError('Required exclusions are missing; this adapter prices VM compute only.')
    if not isinstance(data['items'], list) or not 1 <= len(data['items']) <= 10:
        raise ValueError('Provide 1 to 10 VM items.')
    for item in data['items']:
        if not isinstance(item, dict) or set(item) != {'service', 'region', 'instance', 'os', 'quantity', 'hours'}:
            raise ValueError('Each item needs service, region, instance, os, quantity and hours only.')
        if item['service'] != 'Virtual Machines':
            raise ValueError('Unsupported service: this adapter supports Virtual Machines only.')
        if item['os'] not in ('Windows', 'Linux'):
            raise ValueError('Operating system must be Windows or Linux.')
        if not isinstance(item['region'], str) or not re.fullmatch(r'[A-Za-z0-9 ()-]{2,60}', item['region']):
            raise ValueError('Invalid region label.')
        if not isinstance(item['instance'], str) or not re.fullmatch(r'[A-Za-z0-9 _-]{2,50}', item['instance']):
            raise ValueError('Invalid calculator instance label; use e.g. D2 v3.')
        if type(item['quantity']) is not int or not 1 <= item['quantity'] <= 10000:
            raise ValueError('Quantity must be an integer from 1 to 10000.')
        if type(item['hours']) not in (int, float) or not 0 < item['hours'] <= 744:
            raise ValueError('Hours must be greater than zero and at most 744.')
    return data


class Audit:
    """Local hash chain detects accidental edits; not an immutable compliance store."""
    def __init__(self, directory):
        self.path = Path(directory) / 'audit.jsonl'
        self.previous = '0' * 64
        if self.path.exists() and self.path.stat().st_size:
            last = self.path.read_text(encoding='utf-8').splitlines()[-1]
            self.previous = json.loads(last)['hash']

    def emit(self, event, **fields):
        record = {'time_utc': now(), 'event': event, 'previous_hash': self.previous, **fields}
        record['hash'] = digest(record)
        self.previous = record['hash']
        with self.path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')


@contextlib.contextmanager
def ledger_lock(path):
    lock = Path(str(path) + '.lock')
    handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        yield
    finally:
        os.close(handle)
        lock.unlink()


def reserve_budget(path, amount, limit):
    """Reserve conservative maximum before a request. Failed requests keep reservation."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_lock(path):
        ledger = json.loads(path.read_text()) if path.exists() else {'reserved_usd': 0, 'requests': 0}
        total = Decimal(str(ledger['reserved_usd'])) + Decimal(str(amount))
        if total > Decimal(str(limit)):
            raise ValueError('Local model-test budget exhausted; no request was sent.')
        ledger.update(reserved_usd=float(total), requests=ledger['requests'] + 1)
        save(path, ledger)



def money(text, label):
    match = re.search(r'\b' + re.escape(label) + r':?\s*(?:US)?\$([\d,]+\.\d{2})', text, re.I)
    if not match:
        raise ValueError(f'Missing {label} USD total; refusing to infer a price.')
    return Decimal(match.group(1).replace(',', ''))


def report(path, result):
    rows = ''.join('<tr>' + ''.join(f'<td>{html.escape(str(v))}</td>' for v in
           [r['instance'], r['region'], r['os'], r['quantity'], r['hours'], r['monthly_usd']]) + '</tr>'
           for r in result['items'])
    excluded = ', '.join(result['exclusions'])
    link = result.get('calculator_url')
    share_html = ('<p><a href="' + html.escape(link, quote=True) + '">Open saved Azure Calculator estimate</a></p>') if link else '<p>Share link not yet available.</p>'
    document = f'''<!doctype html><html lang="en"><meta charset="utf-8"><title>Azure BOQ</title>
    <style>body{{font:16px Segoe UI,sans-serif;max-width:1000px;margin:40px auto;color:#17243b}}
    table{{border-collapse:collapse;width:100%}}td,th{{padding:12px;border:1px solid #ccd4df;text-align:left}}
    h1{{color:#005a9e}}small{{color:#526078}}</style><h1>{html.escape(result['name'])}</h1>
    <p>Public USD estimate · {html.escape(result['time_utc'])}</p>
    <table><tr><th>VM instance</th><th>Region</th><th>OS</th><th>Quantity</th><th>Hours</th><th>Monthly USD</th></tr>{rows}</table>
    <h2>Total: US${result['monthly_usd']} / month</h2><p>Upfront: US${result['upfront_usd']}.
    Annual: US${result['annual_usd']}.</p><p><b>Excluded:</b> {html.escape(excluded)}.</p>
    {share_html}
    <p>Estimate only, not a quote or provisioned workload. Public calculator pricing, without customer agreement discounts.</p>
    <small>Source: <a href="{CALCULATOR}">Azure Pricing Calculator</a>. Evidence: calculator.xlsx, screenshots and audit.jsonl in this run folder.</small></html>'''
    Path(path).write_text(document, encoding='utf-8')


def share_estimate(page, directory, audit, wait_for_login=None, notify=lambda message: None):
    from playwright.sync_api import expect
    save_button = page.get_by_role('button', name='Save estimate', exact=True)
    expect(save_button).to_be_visible(timeout=30000)
    if not save_button.is_enabled():
        notify('Waiting for sign-in: use your Microsoft account in Edge and approve MFA on your phone. Up to 10 minutes; no console input needed.')
        page.bring_to_front()
        page.get_by_role('button', name='Log in', exact=True).click()
        # Playwright continues processing popup/navigation events while waiting.
        # The enabled Save control is the completion signal; never bypass MFA.
        if wait_for_login:
            wait_for_login()
        expect(save_button).to_be_enabled(timeout=600000)
    notify('Signed in. Saving estimate and retrieving the Azure Calculator share link...')
    save_button.click()
    page.get_by_role('button', name='Share Estimate', exact=True).click()
    dialog = page.get_by_role('dialog', name='Share Estimate', exact=True)
    textbox = dialog.get_by_role('textbox')
    expect(textbox).to_have_value(re.compile(r'^https://azure\.com/e/[A-Za-z0-9]+$'), timeout=30000)
    url = textbox.input_value()
    if not re.fullmatch(r'https://azure\.com/e/[A-Za-z0-9]+', url):
        raise ValueError('Azure did not return a supported share link; no link was published.')
    (directory / 'calculator-url.txt').write_text(url + '\n', encoding='utf-8')
    dialog.screenshot(path=str(directory / '03-share-link.png'))
    audit.emit('share_link_created', calculator_url=url)
    return url


def calculator_run(args):
    from playwright.sync_api import sync_playwright, expect
    data = validate(json.loads(Path(args.workload).read_text(encoding='utf-8')))
    if not args.reviewed:
        raise ValueError('Review the workload and use --reviewed before execution.')
    run_id = getattr(args, 'run_id', None) or (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + os.urandom(3).hex())
    if not re.fullmatch(r'[A-Za-z0-9-]{1,50}', run_id):
        raise ValueError('Invalid run ID.')
    directory = ROOT / 'runs' / run_id
    directory.mkdir(parents=True)
    audit = Audit(directory)
    save(directory / 'workload.json', data)
    audit.emit('run_started', workload_sha256=digest(data), browser='msedge', public_pricing=True)
    callback = getattr(args, 'notify', lambda message: None)
    def notify(message, state='running'):
        save(directory / 'status.json', {'status': state, 'message': message, 'time_utc': now()})
        print(message, flush=True)
        callback(message)

    notify('Opening Microsoft Edge and Azure Pricing Calculator...')
    page = None
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=False)
        session_path = ROOT.parent / 'profiles' / 'calculator-session.json'
        session = json.loads(session_path.read_text(encoding='utf-8')) if session_path.exists() else None
        context = browser.new_context(accept_downloads=True, locale='en-US', viewport={'width': 1440, 'height': 1000}, storage_state=session)
        try:
            page = context.new_page()
            page.set_default_timeout(30000)
            page.goto(CALCULATOR, wait_until='domcontentloaded', timeout=90000)
            page.get_by_role('textbox', name='Search products', exact=True).wait_for(timeout=90000)
            page.screenshot(path=str(directory / '01-calculator.png'))
            items = []
            for index, item in enumerate(data['items']):
                notify(f'Configuring VM {index + 1} of {len(data["items"])}: {item["instance"]}, {item["region"]}')
                page.get_by_role('textbox', name='Search products', exact=True).fill('Virtual Machines')
                add = page.locator('button[title="Virtual Machines"]').filter(has_text='Add to estimate')
                visible = [x for x in add.all() if x.is_visible()]
                if len(visible) != 1:
                    raise ValueError('Ambiguous VM add control; calculator layout changed.')
                visible[0].click()
                modules = page.locator('.wa-calcService')
                expect(modules).to_have_count(index + 1)
                module = modules.nth(index)
                region = module.get_by_label('Region', exact=True)
                region.select_option(label=item['region'])
                module.get_by_label('Operating system', exact=True).select_option(label=item['os'])
                module.get_by_label('Type', exact=True).select_option(label='(OS Only)')
                module.get_by_label('Tier', exact=True).select_option(label='Standard')
                size = module.locator('input[id="size"]')
                size.wait_for()
                size.click()
                size.fill(item['instance'])
                option = page.get_by_role('option', name=re.compile('^' + re.escape(item['instance']) + ':'))
                expect(option).to_have_count(1)
                option.click()
                module.get_by_role('spinbutton', name='Virtual machines', exact=True).fill(str(item['quantity']))
                module.locator('select[name="hoursFactor"]').select_option(label='Hours')
                module.get_by_role('spinbutton', name='Hours', exact=True).fill(str(item['hours']))
                module.get_by_role('spinbutton', name='Hours', exact=True).press('Tab')
                module.get_by_role('radio', name='Pay as you go', exact=True).check()
                if item['os'] == 'Windows':
                    module.get_by_role('radio', name='License included', exact=True).check()
                module.get_by_role('button', name=re.compile('Bandwidth')).click()
                outbound = module.get_by_role('spinbutton', name=re.compile('^Outbound Data Transfer'))
                outbound.fill('0')
                outbound.press('Tab')
                # Re-check selections after all dependent changes.
                expect(region.locator('option:checked')).to_have_text(item['region'])
                expect(module.get_by_label('Operating system', exact=True).locator('option:checked')).to_have_text(item['os'])
                expect(module.get_by_role('spinbutton', name='Virtual machines', exact=True)).to_have_value(str(item['quantity']))
                expect(module.get_by_role('spinbutton', name='Hours', exact=True)).to_have_value(str(item['hours']))
                expect(outbound).to_have_value('0')
                previous = None
                stable = 0
                for attempt in range(60):
                    text = module.inner_text()
                    if 'Pricing is not available' in text:
                        raise ValueError('Calculator pricing unavailable for requested configuration.')
                    try:
                        totals = tuple(money(text, label) for label in ('Upfront', 'Monthly', 'Annual'))
                    except ValueError:
                        totals = None
                    if totals and totals[1] > 0 and totals == previous:
                        stable += 1
                    else:
                        stable = 0
                    if stable >= 3:
                        break
                    previous = totals
                    page.wait_for_timeout(500)
                else:
                    raise ValueError('Could not verify stable positive pricing.')
                if not re.search(r'0 managed disks', text):
                    raise ValueError('Managed disk exclusion could not be verified.')
                if not re.search(re.escape(item['instance']) + r' \(', text):
                    raise ValueError('Selected instance could not be verified in estimate summary.')
                module.screenshot(path=str(directory / f'item-{index+1:02d}.png'))
                (directory / f'item-{index+1:02d}.txt').write_text(text, encoding='utf-8')
                row = {**item, 'upfront_usd': str(totals[0]), 'monthly_usd': str(totals[1]), 'annual_usd': str(totals[2])}
                items.append(row)
                audit.emit('item_verified', item=index+1, configuration_sha256=digest(item), monthly_usd=row['monthly_usd'])
            page.get_by_label('Currency', exact=True).select_option('USD')
            page.locator('#estimate-name').fill(data['name'])
            with page.expect_download() as download:
                notify('Downloading the verified Excel estimate...')
                page.get_by_role('button', name='Export Estimate', exact=True).click()
            download.value.save_as(str(directory / 'calculator.xlsx'))
            if (directory / 'calculator.xlsx').stat().st_size == 0:
                raise ValueError('Calculator export was empty.')
            share_url = None
            result = {'name': data['name'], 'time_utc': now(), 'currency': 'USD', 'source': CALCULATOR,
                      'calculator_url': share_url,
                      'items': items, 'exclusions': data['exclusions'], 'status': 'verified',
                      **{k: str(sum(Decimal(x[k]) for x in items)) for k in ('upfront_usd', 'monthly_usd', 'annual_usd')}}
            save(directory / 'boq.json', result)
            report(directory / 'boq.html', result)
            notify('Excel and BOQ exported. Preparing sign-in and share link...')
            if not args.no_share:
                share_url = share_estimate(page, directory, audit, getattr(args, 'wait_for_login', None), notify)
                result['calculator_url'] = share_url
                save(directory / 'boq.json', result)
                report(directory / 'boq.html', result)
                # Keep only cookies, not saved calculator items/local storage.
                # Microsoft may still require fresh MFA according to tenant policy.
                state = context.storage_state()
                save(session_path, {'cookies': state['cookies'], 'origins': []})
            page.screenshot(path=str(directory / '02-completed.png'), full_page=True)
            audit.emit('run_completed', result_sha256=digest(result), export_sha256=hashlib.sha256((directory/'calculator.xlsx').read_bytes()).hexdigest())
            print(f'Verified BOQ: {directory / "boq.html"}')
            if share_url:
                print(f'Azure Calculator link: {share_url}')
            notify('Completed. Excel, BOQ and Azure Calculator link are ready.' if share_url else 'Completed. Downloads ready; sharing disabled.', 'completed')
            return directory
        except Exception as error:
            notify('Calculator stopped: ' + type(error).__name__ + '. If Excel already exported it remains available. Check the run log/screenshot; retry sign-in or sharing if required.', 'failed')
            audit.emit('run_failed', error_type=type(error).__name__)
            if page:
                with contextlib.suppress(Exception):
                    page.screenshot(path=str(directory / 'failure.png'))
            save(directory / 'failure.json', {'status': 'failed', 'error_type': type(error).__name__,
                                             'message': str(error)[:1500]})
            raise
        finally:
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    v = sub.add_parser('validate', help='Validate reviewed intake without browser or Azure calls')
    v.add_argument('workload')
    r = sub.add_parser('run', help='Price reviewed VM compute in a separate Edge session')
    r.add_argument('workload')
    r.add_argument('--reviewed', action='store_true')
    r.add_argument('--run-id', help='Local dashboard job ID')
    r.add_argument('--share', dest='no_share', action='store_false', default=True, help='Explicitly allow signed-in save/share')
    args = parser.parse_args()
    if args.command == 'validate':
        validate(json.loads(Path(args.workload).read_text(encoding='utf-8')))
        print('Workload valid.')
    else:
        calculator_run(args)


if __name__ == '__main__':
    main()
