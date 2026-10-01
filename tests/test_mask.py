"""
遮蔽規則。樣本照調查時在三個 repo 裡真的看到的形狀寫，密碼本身是假的。
"""

import pytest

from kb.mask import mask


def test_mongodb_uri_with_credentials_is_masked():
    # DEVLOG.md 那一行的形狀
    r = mask("# 自架:  mongodb://admin:S3cretPa55@traffic-db:27017/?authSource=admin")
    assert "S3cretPa55" not in r.text
    assert "admin:" not in r.text
    assert "mongodb://<USER>:<PASSWORD>@traffic-db:27017/?authSource=admin" in r.text
    assert r.counts == {"uri_credentials": 1}


def test_tailscale_ip_masked_but_private_ips_kept():
    r = mask('ports:\n  - "100.119.53.70:8000:8000"\n  - "127.0.0.1:8000:8000"\n  - "10.140.0.5:3306:3306"')
    assert "100.119.53.70" not in r.text
    assert "<TAILSCALE_IP>:8000:8000" in r.text
    assert "127.0.0.1:8000:8000" in r.text
    assert "10.140.0.5:3306:3306" in r.text
    # 100.x 但不在 CGNAT 網段的不動
    assert mask("100.200.1.1").counts == {}


def test_literal_password_assignment_masked():
    r = mask('MYSQL_ROOT_PASSWORD: ci-only-not-a-secret\npassword = "hunter2hunter2"\nAPI_KEY=abcdefghijklmnop')
    assert "ci-only-not-a-secret" not in r.text
    assert "hunter2hunter2" not in r.text
    assert "abcdefghijklmnop" not in r.text
    assert r.text.count("<REDACTED>") == 3
    assert 'password = "<REDACTED>"' in r.text          # 引號留著，形狀不變


@pytest.mark.parametrize("text", [
    'password=os.environ["MYSQL_PASSWORD"]',          # 程式碼：變數引用
    "AIRFLOW__CORE__FERNET_KEY: ${FERNET_KEY}",        # compose：變數展開
    "POSTGRES_PASSWORD=$(openssl rand -hex 24)",       # 註解裡的指令
    "MYSQL_PASSWORD=",                                 # 空值
    "MYSQL_PASSWORD=\n\n# ---------------------------",  # 空值之後的註解分隔線不是它的值
    "MYSQL_PASSWORD=\nMYSQL_DATABASE=bus",             # 空值之後的下一個 key 不是它的值
    "db.password_hash(user)",
    "token_count = 1234567890",                        # 數字變數，但其實也遮了沒差——這裡確認不遮：值前面是空白等號
])
def test_code_shapes_are_not_masked(text):
    r = mask(text)
    if text.startswith("token_count"):
        # 這一條是刻意的灰色地帶：遮了也可以。只要不把等號前面吃掉。
        assert r.text.startswith("token_count = ")
        return
    assert r.text == text, r.text


def test_bearer_and_known_key_formats():
    r = mask("Authorization: Bearer abcdefghijklmnopqrstuvwxyz0123456789\n"
             "key = sk-ant-api03-abcdefghijklmnopqrstuvwxyz\n"
             "google: AIzaSyA1234567890abcdefghijklmnopqrstuv\n"
             "ghp_abcdefghijklmnopqrstuvwxyzABCDEFGHIJ0123")
    assert "Bearer <TOKEN>" in r.text
    assert "sk-ant" not in r.text and "AIzaSy" not in r.text and "ghp_abc" not in r.text


def test_private_key_block():
    r = mask("x\n-----BEGIN RSA PRIVATE KEY-----\nMIIE...\nabc\n-----END RSA PRIVATE KEY-----\ny")
    assert r.text == "x\n<PRIVATE_KEY>\ny"


def test_email_masked():
    r = mask("authors = [{ name = 'Ethan', email = 'someone@example.com' }]")
    assert "someone@example.com" not in r.text and "<EMAIL>" in r.text


def test_idempotent():
    src = ("mongodb://u:p@h/ Bearer abcdefghijklmnopqrstuvwxyz0123 100.100.1.1 a@b.cc "
           "PASSWORD=longlongsecret")
    once = mask(src).text
    twice = mask(once)
    assert twice.text == once
    assert twice.total == 0


def test_bus_airflow_env_example_shape():
    """bus-airflow/env.example 的實際片段：空值的 key 後面接註解區塊，一個字都不該被遮。"""
    text = ("FERNET_KEY=\nJWT_SECRET=\nPOSTGRES_PASSWORD=\n\n"
            "# ---------------------------------------------------------------------\n"
            "# Web UI 管理員\n"
            "AIRFLOW_ADMIN_USER=ethan\nAIRFLOW_ADMIN_PASSWORD=\nAIRFLOW_ADMIN_EMAIL=someone@example.com\n")
    r = mask(text)
    assert r.counts == {"email": 1}
    assert "<REDACTED>" not in r.text
