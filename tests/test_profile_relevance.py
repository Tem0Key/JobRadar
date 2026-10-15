from pathlib import Path

import pytest

from jobradar.config import load_profiles
from jobradar.filters import evaluate
from jobradar.models import Job


def vacancy(title, description="Manage Palo Alto firewall, VPN and routing."):
    return Job(
        source="lever",
        source_job_id="1",
        source_board="lever:global:example",
        company="Example",
        title=title,
        description=description,
        location="Remote worldwide",
        remote_type="remote",
        url="https://example.com/1",
    )


@pytest.mark.parametrize(
    "title",
    [
        "Account Executive",
        "Senior Customer Engineer",
        "Marketing Manager",
        "Software Engineer, Network Security",
        "GRC Engineer",
        "SOC Compliance Manager",
    ],
)
def test_default_profile_rejects_irrelevant_titles_despite_security_body(title):
    profile = load_profiles(Path("config/profiles.example.yaml")).get("Network Security")
    assert not evaluate(
        vacancy(title, "Palo Alto, firewall, network security and SOC 2."), profile
    ).matched


@pytest.mark.parametrize(
    "title",
    [
        "Network Security Engineer",
        "Senior Firewall Administrator",
        "Information Security Engineer",
        "Инженер сетевой безопасности",
        "Lead Security Engineer",
    ],
)
def test_default_security_profile_accepts_actual_role_titles(title):
    profile = load_profiles(Path("config/profiles.example.yaml")).get("Network Security")
    assert evaluate(vacancy(title), profile).matched


def test_title_exclusions_do_not_reject_normal_collaboration_in_description():
    profile = load_profiles(Path("config/profiles.example.yaml")).get("Network Security")
    job = vacancy(
        "Network Security Engineer", "Manage firewall. Partner with sales and customer teams."
    )
    assert evaluate(job, profile).matched
    job.title = "Network Security Sales Engineer"
    result = evaluate(job, profile)
    assert not result.matched
    assert any("Excluded title keywords" in reason for reason in result.reasons)


def test_default_profile_is_role_based_and_keeps_inventory_separate():
    config = load_profiles(Path("config/profiles.example.yaml"))
    selected = config.get(config.default_profile)
    sales = vacancy("Account Executive", "Palo Alto firewalls, network security, SOC 2.")
    assert not evaluate(sales, selected).matched
    assert evaluate(sales).matched
    assert evaluate(vacancy("Network Engineer", "Operate enterprise networks."), selected).matched
    assert evaluate(vacancy("AI SOC Solutions Architect - Costa Rica"), selected).matched


def test_soc2_company_boilerplate_does_not_match_a_security_operations_role():
    profile = load_profiles(Path("config/profiles.example.yaml")).get("Security Operations")
    assert not evaluate(vacancy("Account Executive", "We are SOC 2 compliant."), profile).matched
    assert evaluate(
        vacancy("SOC Analyst", "Investigate SIEM alerts and incidents."), profile
    ).matched


@pytest.mark.parametrize(
    "title",
    [
        "Senior Identity Security Engineer",
        "IAM Security Operations Engineer",
        "Product Security Engineer",
        "Application Security Analyst",
        "AppSec Security Analyst",
        "Network Security Researcher",
        "SOC Research Engineer",
        "NDR Research Scientist",
        "Security Analyst, Access Management",
        "Аналитик ИБ — управление доступом",
        "Исследователь сетевой безопасности",
        "Lead Security Engineer",
        "Information Security Engineer",
        "Инженер ИБ",
    ],
)
def test_main_profile_rejects_adjacent_and_unspecified_security_roles(title):
    config = load_profiles(Path("config/profiles.example.yaml"))
    profile = config.get(config.default_profile)
    assert not evaluate(vacancy(title), profile).matched


@pytest.mark.parametrize(
    "title",
    [
        "Network Security Engineer",
        "Senior Firewall Administrator",
        "NGFW Administrator",
        "Network Detection and Response Engineer",
        "NDR Analyst",
        "SOC Analyst",
        "AI SOC Solutions Architect - Costa Rica",
        "Security Operations Engineer",
        "Systems Engineer, Network Automation",
        "Network Infrastructure Engineer",
        "Security Infrastructure Engineer",
        "Security Administrator",
        "Detection Engineer",
        "Incident Response Engineer",
        "Инженер по эксплуатации ИБ",
        "Инженер по эксплуатации СЗИ",
        "Инженер по эксплуатации средств защиты информации",
        "Администратор СЗИ",
        "Администратор информационной безопасности",
        "Сетевой инженер",
    ],
)
def test_main_profile_accepts_requested_roles_despite_adjacent_work_in_description(title):
    config = load_profiles(Path("config/profiles.example.yaml"))
    profile = config.get(config.default_profile)
    job = vacancy(
        title,
        "Operate networks and firewalls. Collaborate with IAM, product security, "
        "application security and research teams.",
    )
    assert evaluate(job, profile).matched
