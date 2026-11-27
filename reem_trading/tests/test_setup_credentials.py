import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from eth_account import Account
from dotenv import dotenv_values
import setup_credentials as setup

class SetupTests(unittest.TestCase):
    def test_key_is_never_saved_and_other_settings_survive(self):
        signer=Account.create()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'.env'
            path.write_text('REEM_PRIVATE_KEY=existing-secret\n')
            creds=NS(key='api',secret='secret',passphrase='phrase')
            with patch.object(setup,'getpass',return_value=signer.key.hex()), patch.object(setup,'create_or_derive_api_key_sync',return_value=creds), patch.object(setup,'SyncTransport'):
                setup.source(path)
            values=dotenv_values(path)
            self.assertEqual(values['SOURCE_SIGNER_ADDRESS'],signer.address)
            self.assertEqual(values['SOURCE_API_SECRET'],'secret')
            self.assertEqual(values['REEM_PRIVATE_KEY'],'existing-secret')
            self.assertNotIn(signer.key.hex(),path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
    def test_wrong_source_signer_never_generates_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'.env'
            path.write_text('SOURCE_SIGNER_ADDRESS=0x'+'1'*40+'\n')
            with patch.object(setup,'getpass',return_value=Account.create().key.hex()), patch.object(setup,'create_or_derive_api_key_sync') as create:
                with self.assertRaises(ValueError):
                    setup.source(path)
                create.assert_not_called()
    def test_telegram_uses_only_unique_private_setup_message(self):
        messages=[{'message':{'text':'/setup_reem','chat':{'id':12,'type':'group'},'from':{'id':12}}},
                  {'message':{'text':'/setup_reem','chat':{'id':42,'type':'private'},'from':{'id':42}}}]
        self.assertEqual(setup.telegram_owner(messages),42)
        messages.append({'message':{'text':'/setup_reem','chat':{'id':43,'type':'private'},'from':{'id':43}}})
        with self.assertRaises(ValueError):setup.telegram_owner(messages)
