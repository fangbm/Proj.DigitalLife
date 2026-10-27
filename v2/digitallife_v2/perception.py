"""Bounded UIAutomator observations; absent widget attributes stay unknown."""
from dataclasses import asdict, dataclass, field
import hashlib
import re
import time
import xml.etree.ElementTree as ET

SENSITIVE = re.compile(r"password|credential|one.?time|verification.?code|credit.?card|checkout|payment|biometric|edit.?profile|compose|chat.?input|comment.?input|密码|验证码|银行卡|付款|收银|编辑资料|输入消息|发表评论", re.I)
RISK = re.compile(r"\b(send|comment|post|publish|upload|like|follow|subscribe|favorite|bookmark|share|pay|buy|order|transfer|delete|reset|install|uninstall|grant|permission|login|logout|save|submit|confirm|enable|disable|allow|accept|agree|remove|download)\b|发送|评论|发帖|发布|上传|点赞|关注|收藏|分享|支付|购买|下单|转账|删除|清除|重置|安装|卸载|授权|权限|密码|验证码|登录|退出账号|安全设置|保存|提交|确认|确定|启用|停用|允许|同意|移除|下载", re.I)
READ = re.compile(r"(?:^|[\W_])(navigation|navigate|nav|tab|menu|back|search|result|article|video|player|play|pause|feed|expand|collapse|next|previous|home|category|reader)(?:$|[\W_])|^(搜索|返回|首页|推荐|发现|视频|文章|阅读|播放|暂停|下一集|上一集|目录|更多|展开|收起|取消|search|back|home|explore|discover|play|pause|next|previous|read|browse|menu|cancel)$", re.I)
SEARCH = re.compile(r"search|搜索|搜尋", re.I)


def unlocked_awake(power, policy, displays):
    """Only explicit lock declarations; generic `showing` is delegate-local."""
    lock_names = ("mShowingLockscreen", "mKeyguardShowing", "isKeyguardShowing", "keyguardShowing")
    values, invalid = [], False
    def collect(text, names):
        nonlocal invalid
        for name in names:
            for value in re.findall(r"(?<!\w)" + name + r"\s*[=:]\s*([^\s,;}]+)", text):
                if value not in {"true", "false"}:
                    invalid = True
                else:
                    values.append(value == "true")
    collect(policy + "\n" + displays, lock_names)
    delegate = re.search(r"^(\s*)KeyguardServiceDelegate\s*:?\s*$", policy, re.M)
    if delegate:
        indent = len(delegate[1].split("\n")[-1])
        lines = []
        for line in policy[delegate.end():].splitlines():
            if line.strip() and len(line) - len(line.lstrip()) <= indent:
                break
            lines.append(line)
        collect("\n".join(lines), ("showing", "mIsShowing"))
    if invalid or not values or any(values):
        return False
    awake = re.findall(r"(?<!\w)(?:mInteractive|mWakefulness)\s*=\s*([^\s,;}]+)", power)
    return bool(awake) and all(v in {"true", "Awake"} for v in awake)


@dataclass
class Control:
    control_id: str
    package: str
    resource: str
    text: str
    description: str
    kind: str
    bounds: tuple
    clickable: bool | None
    scrollable: bool | None
    enabled: bool | None
    focused: bool | None
    visible: bool | None
    editable: bool | None
    ancestry: str = ""
    tap_owner: tuple | None = None
    covered: bool = False

    @property
    def is_editor(self):
        return (self.editable is True or "EditText" in self.kind
                or "AutoCompleteTextView" in self.kind or self.kind.endswith(".SearchAutoComplete"))

    @property
    def own_words(self):
        role = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", self.resource)
        return " ".join((role, self.text, self.description)).replace("_", " ")

    @property
    def words(self):
        role = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", self.resource)
        return " ".join((role, self.text, self.description, self.ancestry)).replace("_", " ")

    @property
    def signature(self):
        return (self.package, self.resource, self.text, self.description, self.kind, self.bounds,
                self.clickable, self.scrollable, self.enabled, self.focused, self.visible, self.editable,
                self.ancestry, self.tap_owner, self.covered)


@dataclass
class Observation:
    observation_id: str
    captured_at: str
    serial: str
    foreground: str
    width: int
    height: int
    rotation: int
    controls: list[Control] = field(default_factory=list)
    redacted: bool = False
    incomplete: bool = False
    png_path: str | None = None
    png_sha256: str | None = None
    xml_path: str | None = None
    xml_sha256: str | None = None
    monotonic: float = field(default_factory=time.monotonic)
    vision_allowed: bool = True
    image_withheld_reason: str | None = None
    visual_controls: dict = field(default_factory=dict)
    input_editor: dict = field(default_factory=dict)
    perception_source: str = 'xml_and_screenshot'

    def summary(self):
        d = asdict(self)
        d.pop("monotonic")
        d["controls"] = [asdict(c) for c in self.controls[:100]]
        return d


def parse_xml(raw, observation_id, *, full_access=False):
    start, end = raw.find("<?xml"), raw.rfind("</hierarchy>")
    if start < 0 or end < 0 or len(raw) > 2_000_000:
        raise ValueError("XML_UNAVAILABLE")
    xml = raw[start:end + len("</hierarchy>")]
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise ValueError("XML_UNSAFE")
    root = ET.fromstring(xml)
    nodes = list(root.iter("node"))
    if len(nodes) > 4000:
        raise ValueError("XML_TOO_LARGE")
    redacted = any(n.get("password") == "true" or SENSITIVE.search(" ".join(n.attrib.values()))
                   or (("EditText" in n.get("class", "") or "AutoCompleteTextView" in n.get("class", "")
                        or n.get("class", "").endswith(".SearchAutoComplete") or n.get("editable") == "true")
                       and not SEARCH.search(" ".join(n.attrib.values()))) for n in nodes)
    dialog = any(re.search(r"(?:alertTitle|parentPanel|:id/button1|Dialog)", " ".join(n.attrib.values())) for n in nodes)
    if dialog and any(RISK.search(" ".join(n.attrib.values()).replace("_", " ")) for n in nodes):
        redacted = True
    if full_access:
        redacted = False
    active = focused_layer(root, nodes)
    controls = []
    def visit(node, ancestor="", invisible=False, disabled=False, tap_owner=None):
        if node.tag == "node":
            attrs = node.attrib
            b = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", attrs.get("bounds", ""))
            boolean = lambda name: {"true": True, "false": False}.get(attrs.get(name))
            invisible = invisible or boolean("visible-to-user") is False
            disabled = disabled or boolean("enabled") is False
            if b:
                risk_descendant = ""
                if boolean("clickable") is True and boolean("scrollable") is not True:
                    for descendant in node.iter("node"):
                        if descendant is not node:
                            match = RISK.search(" ".join(descendant.attrib.values()).replace("_", " "))
                            if match:
                                risk_descendant = " " + match[0]
                                break
                covered = active is not None and node not in active
                controls.append(Control(f"{observation_id}:{len(controls)}", attrs.get("package", ""),
                    attrs.get("resource-id", "")[:300], attrs.get("text", "")[:300],
                    attrs.get("content-desc", "")[:300], attrs.get("class", ""), tuple(map(int, b.groups())),
                    boolean("clickable"), boolean("scrollable"), False if disabled else boolean("enabled"), boolean("focused"),
                    False if invisible else boolean("visible-to-user"), boolean("editable"), ancestor + risk_descendant,
                    tap_owner, covered))
                if boolean("clickable") is True:
                    tap_owner = (attrs.get("package", ""), attrs.get("resource-id", ""),
                                 attrs.get("class", ""), tuple(map(int, b.groups())),
                                 False if disabled else boolean("enabled"),
                                 False if invisible else boolean("visible-to-user"),
                                 bool(RISK.search(controls[-1].words)), covered)
            # Parent's action role vetoes a nested apparently harmless label.
            ancestor = " ".join((ancestor, attrs.get("resource-id", ""), attrs.get("text", ""), attrs.get("content-desc", "")))[-1200:]
        for child in node:
            visit(child, ancestor, invisible, disabled, tap_owner)
    visit(root)
    return xml, controls, redacted, int(root.get("rotation", "0"))


def focused_layer(root, nodes):
    """A focused editor in a large, later-drawn sibling pane covers retained UI.

    This is positive XML layer evidence, not an invented visible=true attribute.
    If the required geometry/order is missing, do not infer a layer from names.
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    focused = [n for n in nodes if n.get("focused") == "true"
               and ("EditText" in n.get("class", "") or "AutoCompleteTextView" in n.get("class", "")
                    or n.get("class", "").endswith(".SearchAutoComplete") or n.get("editable") == "true")]
    if len(focused) != 1:
        return None
    def rect(n):
        values = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds", ""))
        return tuple(map(int, values.groups())) if values else None
    frame = rect(nodes[0]) if nodes else None
    if frame is None:
        return None
    width, height = frame[2] - frame[0], frame[3] - frame[1]
    node, scope = focused[0], None
    while node in parents:
        parent = parents[node]
        bounds = rect(node)
        order = node.get("drawing-order", "")
        if (bounds and order.isdigit() and bounds[2] - bounds[0] >= width * .9
                and bounds[3] - bounds[1] >= height * .5):
            for sibling in parent:
                other, other_order = rect(sibling), sibling.get("drawing-order", "")
                if (sibling is not node and other and other_order.isdigit() and int(other_order) < int(order)
                        and max(bounds[0], other[0]) < min(bounds[2], other[2])
                        and max(bounds[1], other[1]) < min(bounds[3], other[3])):
                    scope = node
                    break
        node = parent
    return set(scope.iter("node")) if scope is not None else None


def sha(data):
    return hashlib.sha256(data).hexdigest()
