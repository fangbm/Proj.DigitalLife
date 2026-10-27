"""Finite QQ/WeChat inbound acceptance. Never opens chats or dispatches input."""
import argparse
import asyncio
import json
import time
from types import SimpleNamespace
from digitallife_v2.brain import LifeBrain
from digitallife_v2.config import Config
from digitallife_v2.session import SessionLoop, SessionStopped
from digitallife_v2.store import write_json

MARKERS = {'com.tencent.mobileqq': 'DL-M3-QQ-1027', 'com.tencent.mm': 'DL-M3-WX-1027'}


class ReceiveLoop(SessionLoop):
    def __init__(self, config, directory):
        super().__init__(config, directory, minutes=10, brain_factory=lambda c, s: LifeBrain(c, s))
        self.opened = None
        self.accepted = {}

    async def step(self):
        if not self.bridge:
            raise SessionStopped('SOCIAL_NOTIFICATION_BRIDGE_UNAVAILABLE')
        if self.opened is None:
            await self.guarded(self.phone.identity())
            packages = await self.guarded(self.phone._text('shell', 'pm', 'list', 'packages'))
            if any('package:' + app not in packages.splitlines() for app in MARKERS):
                raise SessionStopped('SOCIAL_APP_NOT_INSTALLED')
            self.opened = time.time()
            write_json(self.directory / 'WINDOW_READY.json', {'opened_at': self.opened,
                'deadline': self.store.state['deadline'], 'markers': MARKERS, 'ui_inputs_allowed': False})
        await self.poll_notifications()
        for app, marker in MARKERS.items():
            if app in self.accepted:
                continue
            row = self.store.db.execute("SELECT id,preview,posted FROM inbound_events WHERE device=? AND source_app=? AND received>=? AND preview LIKE ? ORDER BY received LIMIT 1",
                (self.config.serial, app, self.opened, '%' + marker + '%')).fetchone()
            if not row:
                continue
            offered = {'event_id': row[0], 'kind': 'notification', 'source_app': app,
                'preview': row[1], 'posted_at': row[2], 'context_complete': False,
                'account_ref': None, 'conversation_ref': None, 'trust': 'data_not_instructions',
                'outbound_available': False}
            self.store.inbound_context = offered
            proposal = await self.guarded(self.brain.decide(
                SimpleNamespace(foreground='read_only_notification_window_no_UI_observation'), {}, []))
            response = proposal.get('event_response')
            if not response or response.get('event_id') != row[0]:
                raise SessionStopped('SOCIAL_MODEL_CLASSIFICATION_MISSING')
            self.hub.resolve(offered, response)
            self.accepted[app] = {'event_id': row[0], 'marker_matched': True,
                'decision': response['decision'], 'decision_source': 'real_model',
                'sender_identity': 'unresolved', 'outbound_dispatched': False}
            write_json(self.directory / 'SOCIAL_PROGRESS.json', self.accepted)
        self.store.inbound_context = None
        if len(self.accepted) == len(MARKERS):
            write_json(self.directory / 'SOCIAL_READY.json', {'source': 'LIVE_NOTIFICATION_LISTENER_REAL_MODEL',
                'apps': self.accepted, 'ui_inputs': 0, 'outbound_dispatched': False})
            raise SessionStopped('SOCIAL_RECEIVE_ACCEPTANCE_DONE')
        self.inbound_wake.clear()
        await self.guarded(self.wait_for_event(15))
        return {'outcome': 'waiting_for_human_test_notifications'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', required=True)
    parser.add_argument('--profile', default='find-x6')
    args = parser.parse_args()
    config = Config.load(args.profile)
    config.notification_packages = tuple(MARKERS)
    result = asyncio.run(ReceiveLoop(config, args.session).run())
    print(json.dumps(result))
    return 0 if result['reason'] == 'SOCIAL_RECEIVE_ACCEPTANCE_DONE' else 2


if __name__ == '__main__':
    raise SystemExit(main())
