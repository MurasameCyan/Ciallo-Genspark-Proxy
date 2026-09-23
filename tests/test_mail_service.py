from app.mail_service import (
    build_cf_auth_headers,
    extract_verification_code,
    normalize_mail_api_base,
)


def test_normalize_mail_api_base_removes_api_suffixes():
    assert normalize_mail_api_base("https://mail.example/admin/new_address/") == "https://mail.example"
    assert normalize_mail_api_base("https://mail.example/api") == "https://mail.example"


def test_cf_auth_headers_support_public_and_admin_modes():
    assert build_cf_auth_headers("none", "secret") == {"Content-Type": "application/json"}
    assert build_cf_auth_headers("x-admin-auth", "secret")["x-admin-auth"] == "secret"
    assert build_cf_auth_headers("bearer", "secret")["Authorization"] == "Bearer secret"
    assert build_cf_auth_headers("x-api-key", "secret")["X-API-Key"] == "secret"


def test_extract_verification_code_supports_genspark_formats():
    assert extract_verification_code("Your code is MM0-SF3") == "MM0-SF3"
    assert extract_verification_code("验证码：123456") == "123456"
    assert extract_verification_code("noise 177010") is None
