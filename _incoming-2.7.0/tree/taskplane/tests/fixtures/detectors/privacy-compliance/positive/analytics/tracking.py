def record_event(email_address, consent_given):
    if consent_given:
        send_to_analytics(email_address)
