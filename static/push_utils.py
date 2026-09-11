"""Real Web Push notifications — these arrive even when the admin's
browser/PWA is completely closed, unlike the in-app polling notifications
which only fire while a tab is open. Requires a VAPID key pair (configured
in app.py) and each admin device's push subscription (stored via
PushSubscription, collected client-side through the Push API)."""
import json


def send_push_to_all(app, title, body, url='/admin/'):
    """Sends one push notification to every subscribed admin device.
    Subscriptions the browser has since revoked (HTTP 404/410 from the
    push service) are cleaned up automatically."""
    from pywebpush import webpush, WebPushException
    from models import db, PushSubscription

    subs = PushSubscription.query.all()
    if not subs:
        return 0

    vapid_private_key = app.config.get('VAPID_PRIVATE_KEY', '')
    vapid_claims = {'sub': app.config.get('VAPID_CLAIMS_EMAIL', 'mailto:admin@example.com')}
    payload = json.dumps({'title': title, 'body': body, 'url': url})

    sent = 0
    stale_ids = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub.to_push_dict(),
                data=payload,
                vapid_private_key=vapid_private_key,
                vapid_claims=dict(vapid_claims),
            )
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, 'status_code', None)
            if status in (404, 410):
                stale_ids.append(sub.id)
            else:
                print(f"[push] send failed ({status}): {e}", flush=True)
        except Exception as e:
            print(f"[push] send failed: {e}", flush=True)

    if stale_ids:
        PushSubscription.query.filter(PushSubscription.id.in_(stale_ids)).delete(synchronize_session=False)
        db.session.commit()

    return sent
