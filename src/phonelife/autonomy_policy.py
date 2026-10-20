"""Fail-closed autonomous capability gate, additional to the original ActionPolicy."""
import re
import xml.etree.ElementTree as ET
from .policy import PolicyDenied


class AutonomousActionGate:
    # Capability routes, not goals. These labels only open read-only Settings subpages.
    settings_routes = frozenset({'about phone', 'about emulated device', '关于手机', '关于模拟设备',
        'android version', 'android 版本', 'model & hardware', '型号和硬件', 'battery', '电池',
        'storage', '存储', '存储空间', 'display', '显示', 'system', '系统', 'memory', '内存'})
    unsafe = re.compile(r'pay|purchase|transfer|send|publish|delete|erase|reset|install|grant|password|account|permission|'
                        r'支付|转账|购买|发帖|发送|删除|清除|重置|安装|授予|密码|账号|权限', re.I)
    packages = frozenset({'com.android.settings'})

    @classmethod
    def check_goal(cls, goal, limits, unfinished):
        if goal.risk_class != 'low' or cls.unsafe.search(goal.description):
            raise PolicyDenied('Autonomous medium/high or sensitive objectives have no write capability')
        if goal.estimated_cost > limits.max_actions:
            raise PolicyDenied('Goal exceeds action budget')
        if goal.continuation_of and goal.continuation_of not in unfinished:
            raise PolicyDenied('Continuation requires an eligible persisted goal')
        def check(spec):
            if 'package' in spec and spec['package'] not in cls.packages:
                raise PolicyDenied('Goal verification requests an unavailable autonomous app capability')
            for child in spec.get('checks', []):
                check(child)
        check(goal.verification)

    @staticmethod
    def bounds(node):
        match = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', node.get('bounds', ''))
        return tuple(map(int, match.groups())) if match else (0, 0, 0, 0)

    @classmethod
    def inspect(cls, action, xml, foreground, width, height):
        if 'com.android.settings/' not in foreground and foreground != 'com.android.settings':
            raise PolicyDenied('Autonomous touch requires a supported foreground app')
        if len(xml) > 2_000_000 or '<!DOCTYPE' in xml or '<!ENTITY' in xml:
            raise PolicyDenied('Unsafe UI hierarchy')
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            raise PolicyDenied('No reliable UI control identity; touch refused') from None
        nodes = list(root.iter('node'))
        def contains(node, x, y):
            a,b,c,d = cls.bounds(node)
            return a <= x < c and b <= y < d
        touched = [n for n in nodes if contains(n, action.x, action.y)]
        if any(n.get('package') not in {'com.android.settings', ''} for n in touched):
            raise PolicyDenied('Overlapping foreign UI at touch point')
        if not (.08 * height < action.y < .92 * height and .05 * width < action.x < .95 * width):
            raise PolicyDenied('Autonomous touch outside application body')
        if any(n.get('checkable') == 'true' or any(s in n.get('class', '') for s in ('Switch', 'SeekBar', 'EditText', 'CheckBox')) for n in touched):
            raise PolicyDenied('State-changing control has no autonomous capability')
        if action.type == 'swipe':
            if abs(action.x2-action.x) > width * .1:
                raise PolicyDenied('Only vertical list scrolling is allowed')
            if not any(n.get('scrollable') == 'true' and contains(n, action.x2, action.y2) for n in touched):
                raise PolicyDenied('Swipe is not bound to an identified scrollable list')
            return
        candidates = [n for n in touched if n.get('clickable') == 'true' and n.get('enabled') != 'false']
        candidates.sort(key=lambda n: (cls.bounds(n)[2]-cls.bounds(n)[0]) * (cls.bounds(n)[3]-cls.bounds(n)[1]))
        if not candidates:
            raise PolicyDenied('No identified clickable navigation control')
        target = candidates[0]
        descendants = list(target.iter('node'))
        labels = {n.get('text', '').strip().casefold() for n in descendants}
        if any(n.get('checkable') == 'true' for n in descendants) or cls.unsafe.search(' '.join(labels)):
            raise PolicyDenied('Sensitive or state-changing control refused')
        if not labels & cls.settings_routes or target.get('package') != 'com.android.settings':
            raise PolicyDenied('Unknown navigation control; autonomous tap refused')

    async def check(self, phone, action, observation):
        if action.type in {'home', 'back', 'finish'}:
            return {'capability': 'navigation_or_stop'}
        if action.type == 'launch':
            if action.package not in self.packages:
                raise PolicyDenied('No autonomous capability for this app; launch allowlist alone is insufficient')
            return {'capability': 'launch_readonly_environment'}
        if action.type == 'text':
            raise PolicyDenied('Autonomous free-form text entry is disabled')
        try:
            xml = await phone.hierarchy()
            foreground = await phone.foreground()
            fresh = await phone.observe()
        except (RuntimeError, ValueError, OSError):
            raise PolicyDenied('UI evidence unavailable; autonomous touch refused') from None
        from .progress import ActionProgressDetector
        detector = ActionProgressDetector()
        if detector._body(fresh)[0] != detector._body(observation)[0]:
            raise PolicyDenied('Application content changed while identifying the control')
        self.inspect(action, xml, foreground, observation.width, observation.height)
        return {'capability': 'identified_readonly_settings_navigation', 'observation_sha256': fresh.sha256}
