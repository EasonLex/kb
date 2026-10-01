"""
遮蔽敏感資訊。在切段與算 hash「之前」對整份文件做。

設計原則
------------------------------------------------------------
  寧可多遮不要漏遮——但也不能把程式碼遮到看不懂。
  所以 key=value 那條規則只遮「看起來像字面值的秘密」：
  `password=os.environ["X"]`、`${POSTGRES_PASSWORD}`、`MYSQL_PASSWORD=`（空值）
  都保留，它們是設定的「形狀」，不是秘密本身。

  每條規則的替代字串都是 <...> 形式，而且遮蔽是幂等的：
  mask(mask(x)) == mask(x)。測試會驗。

已知會命中的真實樣本（來自調查）：
  - TrafficProject/DEVLOG.md 有一行完整的 mongodb://admin:<密碼>@traffic-db:27017/...
  - deploy/docker-compose.yml 與 DEVLOG 裡的 Tailscale IP（100.64.0.0/10）
  - pyproject.toml 與 env.example 裡的 email
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 值裡出現這些字元就不是字面值秘密（是程式碼或變數引用）
_NOT_LITERAL = r"(?![^\s\"'`]*[\(\[\$\{])"

RULES: list[tuple[str, re.Pattern, str]] = [
    ("private_key",
     re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
     "<PRIVATE_KEY>"),
    # scheme://user:password@host（排除 <>，不然第二次會把自己的 <USER>:<PASSWORD> 再遮一次）
    ("uri_credentials",
     re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@'\"<>]+):([^/\s@'\"<>]+)@"),
     r"\1<USER>:<PASSWORD>@"),
    ("bearer",
     re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9\-._~+/]{20,}=*"),
     r"\1 <TOKEN>"),
    ("jwt",
     re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
     "<JWT>"),
    # 常見金鑰格式：OpenAI/Anthropic sk-、Google AIza、GitHub ghp_/gho_、AWS AKIA、Slack xox、Voyage pa-
    ("known_key_format",
     re.compile(r"\b(sk-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{35}|gh[pousr]_[A-Za-z0-9]{36,}"
                r"|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9\-]{10,}|pa-[A-Za-z0-9_\-]{20,})\b"),
     "<API_KEY>"),
    # password=xxxx / SECRET: "xxxx" / token = 'xxxx'——只遮字面值，見檔頭
    # 等號兩邊只允許空格與 tab，不允許換行：`POSTGRES_PASSWORD=`（空值）的下一行不是它的值。
    ("key_value_secret",
     re.compile(r"(?i)\b([A-Za-z0-9_\-]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key)[A-Za-z0-9_\-]*)"
                r"([ \t]*[=:][ \t]*)([\"']?)" + _NOT_LITERAL + r"(?!#)([^\s\"'`<>,;]{8,})"),
     r"\1\2\3<REDACTED>"),
    # Tailscale 的 CGNAT 網段 100.64.0.0/10
    ("tailscale_ip",
     re.compile(r"\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
     "<TAILSCALE_IP>"),
    ("email",
     re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
     "<EMAIL>"),
]


@dataclass
class MaskResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def mask(text: str) -> MaskResult:
    counts: dict[str, int] = {}
    for name, pattern, repl in RULES:
        text, n = pattern.subn(repl, text)
        if n:
            counts[name] = n
    return MaskResult(text, counts)
