"""Private Telegram controls and durable operator notifications."""
import logging
import time
from datetime import datetime, timezone

logger = logging.getLogger('ReemTrading.Operator')


class Operator:
    def __init__(self, store, http, token, user_id):
        self.store, self.http = store, http
        self.token, self.user_id = token, int(user_id)
        if self.user_id <= 0:
            raise ValueError('REEM_TELEGRAM_USER_ID must be a positive private-chat user ID')
        self.online = False
        self.error = None
        self.open_orders = None
        self.last_check = None
        self.failed = False
        self.started = time.time()
        self.last_contact = None
        self.last_reminder = 0

    def api(self, method, **data):
        # Never log HTTP exception text: Telegram URLs contain the bot token.
        response = self.http.post(f'https://api.telegram.org/bot{self.token}/{method}', json=data, timeout=5)
        response.raise_for_status()
        payload = response.json()
        if not payload.get('ok'):
            raise ValueError('Telegram request rejected')
        return payload['result']

    def queue(self, key, text):
        with self.store.db:
            self.store.notice(key, text)

    def status(self):
        state = 'STOPPED BY ERROR' if self.error else 'PAUSED' if self.store.paused else 'RUNNING'
        if not self.online or self.failed:
            state += ' — CONNECTION PROBLEM; no new trading actions'
        checked = datetime.fromtimestamp(self.last_check, timezone.utc).isoformat() if self.last_check else 'not yet'
        orders = str(self.open_orders) + ' (last successful scan)' if self.open_orders is not None else 'unknown'
        text = (f'Reem copy bot: {state}\nLast successful account check: {checked}\n'
                f'Open orders (all Reem orders): {orders}\nExisting orders can still fill while paused.\n'
                f'Pending notifications: {self.store.db.execute("SELECT COUNT(*) FROM notices WHERE sent=0").fetchone()[0]}')
        if self.error:
            text += '\nError: ' + self.error + '\nReview and reconcile before restarting; Resume cannot clear errors.'
        return text

    def command(self, text, update_id):
        text = text.strip().lower()
        if text == '/pause':
            with self.store.db:
                self.store.db.execute("INSERT OR REPLACE INTO controls VALUES ('paused','1')")
            reply = ('PAUSED. No new orders, changes or cancellations. Existing orders and positions are untouched. '
                     'Orders submitted before this acknowledgement may still fill.')
        elif text == '/resume confirm' and not self.error:
            with self.store.db:
                self.store.db.execute("INSERT OR REPLACE INTO controls VALUES ('paused','0')")
            reply = 'Resumed. Missed source activity will be processed using the saved history.'
        elif text.startswith('/resume'):
            reply = ('Resume is blocked by an error; review it first.' if self.error else
                     'Resuming will catch up on activity missed while paused, which may place delayed trades. '
                     'To explicitly accept this, send /resume confirm. Otherwise remain paused.')
        else:
            reply = self.status() + '\n/status — refresh\n/pause — stop trading actions\n/resume — review catch-up warning'
        self.store.notice(f'command:{update_id}', reply)

    def poll(self):
        try:
            row = self.store.db.execute("SELECT value FROM controls WHERE name='telegram_offset'").fetchone()
            updates = self.api('getUpdates', offset=int(row[0]) if row else 0,
                               timeout=0, allowed_updates=['message'])
            for update in updates:
                message = update.get('message', {})
                chat, sender = message.get('chat', {}), message.get('from', {})
                with self.store.db:
                    if (chat.get('type') == 'private' and chat.get('id') == self.user_id
                            and sender.get('id') == self.user_id and not sender.get('is_bot', False)):
                        self.command(message.get('text', ''), update['update_id'])
                    self.store.db.execute("INSERT OR REPLACE INTO controls VALUES ('telegram_offset',?)",
                                          (str(update['update_id'] + 1),))
            if self.last_contact is not None and not self.online:
                self.queue(f'telegram-recovered:{time.time_ns()}', 'Telegram connection restored; queued notices follow.')
            self.online = True
            self.last_contact = time.time()
            return True
        except Exception as exc:
            logger.warning('Telegram control unavailable (%s); no new trading actions', type(exc).__name__)
            if self.online:
                self.queue(f'telegram-lost:{time.time_ns()}', 'Telegram connection was lost. Trading actions were suspended until controls recovered.')
            self.online = False
            return False

    def flush(self):
        try:
            # Bound delivery work so notification bursts cannot starve Pause requests.
            for row in self.store.db.execute("SELECT * FROM notices WHERE sent=0 ORDER BY CASE WHEN id LIKE 'command:%' OR id LIKE 'halted:%' THEN 0 WHEN id LIKE 'connection:%' THEN 1 ELSE 2 END, rowid LIMIT 5").fetchall():
                self.api('sendMessage', chat_id=self.user_id, text=row['text'][:4000],
                         reply_markup={'keyboard':[['/status', '/pause'], ['/resume']], 'resize_keyboard':True})
                with self.store.db:
                    self.store.db.execute('UPDATE notices SET sent=1 WHERE id=?', (row['id'],))
            return True
        except Exception as exc:
            logger.warning('Telegram notification pending (%s)', type(exc).__name__)
            self.online = False
            return False

    def connection_failed(self, reason):
        now = time.time()
        if not self.failed or now - self.last_reminder >= 300:
            self.queue(f'connection:{time.time_ns()}', f'Connection lost ({reason}). No new trading actions until a complete successful check. Existing orders may still fill.')
            self.last_reminder = now
        self.failed = True

    def checked(self, open_orders):
        if self.failed:
            self.queue(f'recovered:{time.time_ns()}', 'Connection restored. Complete account checks are succeeding again.')
        self.failed = False
        self.last_check = time.time()
        self.open_orders = open_orders

    def halt(self, reason):
        # Detailed exceptions remain in local logs, never send SDK credentials or URLs.
        self.error = reason
        self.queue(f'halted:{time.time_ns()}', 'Copying STOPPED BY ERROR: ' + reason + '\nReview the local logs and saved operation records before restarting.')
