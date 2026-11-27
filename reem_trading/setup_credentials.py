"""Generate source credentials or identify Reem's Telegram user without trading."""
import argparse
import logging
import os
import tempfile
import time
import warnings
from getpass import getpass, GetPassWarning
from pathlib import Path

import httpx
from dotenv import dotenv_values, set_key
from eth_account import Account
from polymarket._internal.actions.auth import create_or_derive_api_key_sync
from polymarket._internal.l1_auth import sign_api_key_auth
from polymarket.clients._transport import SyncTransport


class SetupError(ValueError):
    """A setup instruction safe to display without exposing credentials."""


def save(path, values):
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.credential-setup-')
    try:
        with os.fdopen(descriptor, 'w') as output:
            output.write(path.read_text() if path.exists() else '')
        for key, value in values.items():
            set_key(temporary, key, str(value))
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        Path(temporary).unlink(missing_ok=True)


def source(path):
    values = dotenv_values(path)
    with warnings.catch_warnings():
        warnings.simplefilter('error', GetPassWarning)
        key = getpass('YOUR source-account signing private key (hidden; not saved): ').strip()
    signer = Account.from_key(key)
    expected = values.get('SOURCE_SIGNER_ADDRESS')
    if expected and expected.lower() != signer.address.lower():
        raise SetupError('Key does not match SOURCE_SIGNER_ADDRESS. Confirm that field is the signer, not the profile wallet; no credentials generated.')
    # Use the pinned SDK's auth helpers only; a full client can deploy a wallet.
    transport = SyncTransport(base_url='https://clob.polymarket.com')
    try:
        signature = sign_api_key_auth(signer, chain_id=137, timestamp=int(time.time()), nonce=0)
        credentials = create_or_derive_api_key_sync(transport, signature)
    finally:
        transport.close()
    save(path, {'SOURCE_SIGNER_ADDRESS': signer.address, 'SOURCE_API_KEY': credentials.key,
                'SOURCE_API_SECRET': credentials.secret, 'SOURCE_API_PASSPHRASE': credentials.passphrase})
    print('Source signer and three API credentials saved privately. No source private key saved; no trades placed.')


def telegram_owner(updates):
    owners = set()
    for update in updates:
        message = update.get('message', {})
        chat, sender = message.get('chat', {}), message.get('from', {})
        if (message.get('text', '').strip() == '/setup_reem' and chat.get('type') == 'private'
                and chat.get('id') == sender.get('id') and not sender.get('is_bot', False)
                and isinstance(sender.get('id'), int) and sender['id'] > 0):
            owners.add(sender['id'])
    if len(owners) != 1:
        raise SetupError('Expected one private /setup_reem sender. Ask Reem to send it to the dedicated bot. Multiple candidates require manual user-ID verification.')
    return owners.pop()


def telegram(path):
    values = dotenv_values(path)
    token = (values.get('REEM_TELEGRAM_BOT_TOKEN') or '').strip()
    if not token:
        raise SetupError('Fill REEM_TELEGRAM_BOT_TOKEN in .env first.')
    with httpx.Client(timeout=15) as http:
        response = http.post(f'https://api.telegram.org/bot{token}/getUpdates',
                             json={'timeout': 0, 'limit': 100, 'allowed_updates': ['message']})
        response.raise_for_status()
        data = response.json()
        if not data.get('ok'):
            raise SetupError('Telegram rejected the request; check the token and webhook settings.')
        owner = telegram_owner(data['result'])
    existing = values.get('REEM_TELEGRAM_USER_ID')
    if existing and existing != str(owner):
        raise SetupError('The setup sender differs from the configured user; verify the owner manually.')
    save(path, {'REEM_TELEGRAM_USER_ID': owner})
    print('Telegram user ID saved:', owner)
    print('Confirm this is Reem’s account. No Telegram messages sent and no copying started.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=['source', 'telegram'])
    args = parser.parse_args()
    path = Path(__file__).resolve().with_name('.env')
    if not path.exists():
        raise SetupError('Create .env from .env.example first.')
    path.chmod(0o600)
    (source if args.step == 'source' else telegram)(path)


if __name__ == '__main__':
    logging.getLogger('httpx').setLevel(logging.WARNING)
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        # SDK/HTTP exception text can contain sensitive URLs or response bodies.
        if isinstance(exc, SetupError):
            print(str(exc))
        else:
            print('Setup did not complete (' + type(exc).__name__ + '). Check the key format, connection, and whether hidden terminal input is available. No credentials were displayed.')
        raise SystemExit(1) from None
