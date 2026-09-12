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
    from py_vapid import Vapid02
    from models import db, PushSubscription

    subs = PushSubscription.query.all()
    if not subs:
        print("[push] no subscriptions registered — nothing to send", flush=True)
        return 0

    pem = app.config.get('VAPID_PRIVATE_KEY', '')
    try:
        # webpush() only accepts a raw string in a very specific undocumented
        # shape (no PEM headers) or a file path — passing the actual PEM
        # text directly makes it try to base64-decode the "-----BEGIN..."
        # markers themselves and fail. Loading it into a Vapid02 instance
        # first sidesteps that entirely: pywebpush accepts a Vapid instance
        # as-is and calls .sign() on it directly.
        vapid_key = Vapid02.from_pem(pem.encode() if isinstance(pem, str) else pem)
    except Exception as e:
        print(f"[push] could not load VAPID private key: {e}", flush=True)
        return 0

    vapid_claims = {'sub': app.config.get('VAPID_CLAIMS_EMAIL', 'mailto:admin@example.com')}
    payload = json.dumps({'title': title, 'body': body, 'url': url})

    sent = 0
    stale_ids = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub.to_push_dict(),
                data=payload,
                vapid_private_key=vapid_key,
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

    print(f"[push] sent to {sent}/{len(subs)} device(s)", flush=True)
    return sent
