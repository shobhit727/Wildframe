#!/usr/bin/env python3
import subprocess
from pathlib import Path

CHART = Path(__file__).resolve().parent.parent / "wildframe"
PROD = Path(__file__).resolve().parent.parent / "values-production.yaml"


def helm_template(namespace, values_file, extra_sets):
    cmd = ["helm", "template", "wildframe", str(CHART), "--namespace", namespace]
    if values_file:
        cmd += ["-f", str(values_file)]
    for k, v in extra_sets:
        cmd += ["--set", f"{k}={v}"]
    return subprocess.run(cmd, capture_output=True, text=True)


def assert_success(res, label):
    assert res.returncode == 0, f"{label} should succeed but failed: {res.stderr}"


def assert_fail(res, label):
    assert res.returncode != 0, f"{label} should fail but succeeded"


def test_dev_renders():
    res = helm_template("wildframe", None, [])
    assert_success(res, "dev default")


def test_prod_renders_with_external():
    res = helm_template("wildframe-production", PROD, [])
    assert_success(res, "prod with external")
    assert 'name: KAFKA_SSL_INSECURE\n          value: "false"' in res.stdout


def test_prod_fails_when_kafka_tls_verification_is_disabled():
    res = helm_template(
        "wildframe-production", PROD, [("kafka.sasl.insecureSkipVerify", "true")]
    )
    assert_fail(res, "prod with Kafka TLS verification disabled")


def test_prod_fails_without_postgres_enabled():
    res = helm_template("wildframe-production", PROD, [("external.postgres.enabled", "false")])
    assert_fail(res, "prod without postgres enabled")


def test_prod_fails_without_redis_enabled():
    res = helm_template("wildframe-production", PROD, [("external.redis.enabled", "false")])
    assert_fail(res, "prod without redis enabled")


def test_prod_fails_with_incluster_postgresHost():
    res = helm_template("wildframe-production", PROD, [("infra.postgresHost", "postgres")])
    assert_fail(res, "prod with incluster postgresHost")


def test_prod_fails_with_incluster_postgresHost_prod():
    res = helm_template("wildframe-production", PROD, [("infra.postgresHost", "postgres-prod")])
    assert_fail(res, "prod with postgres-prod host")


def test_prod_fails_with_incluster_redisHost():
    res = helm_template("wildframe-production", PROD, [("infra.redisHost", "redis-master")])
    assert_fail(res, "prod with incluster redisHost")


def test_prod_fails_with_empty_postgresHost():
    res = helm_template("wildframe-production", PROD, [("infra.postgresHost", "")])
    assert_fail(res, "prod with empty postgresHost")


def test_prod_fails_with_empty_redisHost():
    res = helm_template("wildframe-production", PROD, [("infra.redisHost", "")])
    assert_fail(res, "prod with empty redisHost")


def test_prod_fails_without_cidrs():
    res = helm_template(
        "wildframe-production",
        PROD,
        [("external.postgres.cidrs", "null"), ("external.postgres.fqdns", "null")],
    )
    assert_fail(res, "prod without postgres cidrs/fqdns")


def test_prod_fails_with_dev_defaults_in_prod_namespace():
    res = helm_template("wildframe-production", None, [])
    assert_fail(res, "prod namespace with dev defaults should fail")


def test_prod_fails_with_set_empty_via_prod_values_override():
    cmd = [
        "helm",
        "template",
        "wildframe",
        str(CHART),
        "--namespace",
        "wildframe-production",
        "-f",
        str(PROD),
        "--set",
        "external.postgres.cidrs=null",
        "--set",
        "external.postgres.fqdns=null",
        "--set",
        "external.redis.cidrs=null",
        "--set",
        "external.redis.fqdns=null",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    assert_fail(res, "prod with empty cidrs/fqdns via --set")


if __name__ == "__main__":
    test_dev_renders()
    print("PASS dev renders")
    test_prod_renders_with_external()
    print("PASS prod renders")
    test_prod_fails_without_postgres_enabled()
    print("PASS prod fails without postgres enabled")
    test_prod_fails_without_redis_enabled()
    print("PASS prod fails without redis enabled")
    test_prod_fails_with_incluster_postgresHost()
    print("PASS prod fails incluster postgresHost")
    test_prod_fails_with_incluster_postgresHost_prod()
    print("PASS prod fails postgres-prod host")
    test_prod_fails_with_incluster_redisHost()
    print("PASS prod fails incluster redisHost")
    test_prod_fails_with_empty_postgresHost()
    print("PASS prod fails empty postgresHost")
    test_prod_fails_with_empty_redisHost()
    print("PASS prod fails empty redisHost")
    test_prod_fails_without_cidrs()
    print("PASS prod fails without cidrs")
    test_prod_fails_with_dev_defaults_in_prod_namespace()
    print("PASS prod fails with dev defaults")
    test_prod_fails_with_set_empty_via_prod_values_override()
    print("PASS prod fails with empty cidrs via set")
    print("ALL PASS")
