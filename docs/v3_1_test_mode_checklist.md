# V3.1 Razorpay Test Mode Certification Checklist

The offline release candidate remains distinct from full Test Mode certification.

Required before the `v3.1-certified` tag:

- [ ] `RAZORPAY_KEY_ID` is a Test Mode key (`rzp_test_...`).
- [ ] `RAZORPAY_KEY_SECRET` is present only in environment/secrets storage.
- [ ] `RAZORPAY_WEBHOOK_SECRET` is present only in environment/secrets storage.
- [ ] Create a small INR Order through the server-side Orders API.
- [ ] Fetch that Order back and verify ID, amount, currency, receipt and expected state.
- [ ] Complete Standard Checkout in Test Mode.
- [ ] Verify Checkout signature server-side with the stored Order ID.
- [ ] Receive a real signed payment webhook and validate the raw body.
- [ ] Confirm duplicate webhook delivery is idempotent.
- [ ] Reconcile the resulting Order/payments through Razorpay APIs.
- [ ] Confirm no real/live key or real-money path was exercised.

Do not print or persist API secrets in certification evidence.
