"""Domain configuration for the multi-domain SupportAI demo."""

DOMAINS = {
    "food_delivery": {
        "name": "Food delivery",
        "description": "Orders, delivery delays, missing items, and food quality.",
        "team": "Food Operations",
        "category_teams": {"food_quality": "Food Safety Team", "payments": "Payments Support", "delivery": "Delivery Operations"},
        "categories": ["orders", "delivery", "missing_items", "food_quality", "payments", "account", "general"],
        "policies": [
            "Never promise a refund or redelivery until the order and eligibility are verified.",
            "For food safety or allergic reaction reports, escalate immediately and do not offer medical advice.",
        ],
        "policy_checks": {
            "blocked_reply_phrases": ["your refund is approved", "we have refunded", "we have redelivered"],
            "escalation_terms": ["allergy", "allergic reaction", "food poisoning"],
        },
    },
    "edtech": {
        "name": "Edtech",
        "description": "Course access, classes, learning content, subscriptions, and instructors.",
        "team": "Learner Support",
        "category_teams": {"instructors": "Academic Support", "payments": "Billing Support", "course_access": "Learning Platform Support"},
        "categories": ["course_access", "classes", "content", "instructors", "payments", "account", "general"],
        "policies": [
            "Do not guarantee grades, certificates, admission, or learning outcomes.",
            "Verify enrollment and subscription status before promising access or a refund.",
        ],
        "policy_checks": {
            "blocked_reply_phrases": ["guaranteed grade", "guaranteed admission", "guaranteed certificate", "guaranteed learning outcome"],
            "escalation_terms": ["instructor misconduct", "accessibility complaint"],
        },
    },
    "ecommerce": {
        "name": "Ecommerce",
        "description": "Orders, shipping, damaged items, returns, billing, and accounts.",
        "team": "Customer Support",
        "category_teams": {"shipping": "Fulfillment Support", "returns": "Returns Team", "billing": "Billing Support", "product": "Product Support"},
        "categories": ["shipping", "billing", "returns", "product", "technical", "account", "general"],
        "policies": [
            "Verify the order and eligibility before promising a refund or replacement.",
            "Never ask customers to share passwords or full payment card numbers.",
        ],
        "policy_checks": {
            "blocked_reply_phrases": ["your refund is approved", "we have refunded", "your replacement is confirmed", "we have replaced"],
            "escalation_terms": ["unauthorized access", "fraud", "injury"],
        },
    },
    "healthcare": {
        "name": "Healthcare",
        "description": "Appointments, records, prescriptions, billing, and patient portal access.",
        "team": "Patient Services",
        "category_teams": {"prescriptions": "Pharmacy Support", "records": "Medical Records Team", "appointments": "Care Coordination"},
        "categories": ["appointments", "prescriptions", "records", "billing", "portal_access", "general"],
        "policies": [
            "Do not diagnose, recommend treatment, or interpret symptoms; direct clinical questions to a qualified clinician.",
            "Do not request sensitive health details in an unsecured channel; verify identity using approved procedures.",
            "For emergencies, tell the customer to contact local emergency services immediately.",
        ],
        "policy_checks": {
            "blocked_reply_phrases": ["you have a diagnosis", "you should take", "your symptoms mean", "send your full medical record"],
            "escalation_terms": ["chest pain", "can't breathe", "cannot breathe", "emergency", "suicidal"],
        },
    },
    "travel": {
        "name": "Travel",
        "description": "Bookings, itinerary changes, cancellations, baggage, and disruptions.",
        "team": "Travel Support",
        "category_teams": {"baggage": "Baggage Services", "itinerary_changes": "Disruption Desk", "payments": "Travel Billing"},
        "categories": ["bookings", "itinerary_changes", "cancellations", "baggage", "payments", "general"],
        "policies": [
            "Verify the booking and fare conditions before promising a change, cancellation, refund, or seat.",
            "For immediate safety concerns, direct the traveler to local emergency services or on-site staff.",
        ],
        "policy_checks": {
            "blocked_reply_phrases": ["your refund is approved", "your booking is changed", "your seat is confirmed"],
            "escalation_terms": ["stranded", "safety", "missed connection", "injury"],
        },
    },
    "telecom": {
        "name": "Telecom",
        "description": "Connectivity, plans, devices, billing, SIM, and account access.",
        "team": "Customer Care",
        "category_teams": {"connectivity": "Network Operations", "billing": "Billing Support", "account": "Account Security"},
        "categories": ["connectivity", "plans", "devices", "billing", "account", "general"],
        "policies": [
            "Verify the account holder through approved procedures before changing a plan, SIM, or account security setting.",
            "Do not claim an outage or promise a credit until service status and eligibility are checked.",
        ],
        "policy_checks": {
            "blocked_reply_phrases": ["your credit is approved", "your plan has been changed", "your sim has been activated"],
            "escalation_terms": ["sim swap", "identity theft", "account takeover"],
        },
    },
}


def get_domain(domain_id: str) -> dict:
    """Return a domain configuration or raise a readable validation error."""
    try:
        return DOMAINS[domain_id]
    except KeyError as error:
        choices = ", ".join(DOMAINS)
        raise ValueError(f"Unknown domain '{domain_id}'. Choose one of: {choices}.") from error


def public_domains(*, include_policies: bool = True) -> list[dict]:
    """Return domain metadata suitable for the browser UI/API."""
    result = []
    for domain_id, config in DOMAINS.items():
        item = {
            "id": domain_id,
            "name": config["name"],
            "description": config["description"],
            "categories": config["categories"],
            "team": config.get("team", "Customer Support"),
        }
        if include_policies:
            item["policies"] = config["policies"]
        result.append(item)
    return result
