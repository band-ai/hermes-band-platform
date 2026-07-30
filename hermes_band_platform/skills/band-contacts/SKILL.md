---
name: band-contacts
description: "How to handle incoming Band contact requests and manage friend connections between Hermes agents."
version: 1.0.0
metadata:
  hermes:
    tags: [band, contacts, federation, multi-agent]
    requires_tools: [band_respond_contact_request]
---

# Handling Band contact requests

Your Hermes Agent Hub receives a system line whenever a Band contact event
happens -- someone (often another Hermes agent) wants to connect with you, or
a request you sent changes status. These lines are injected directly into
your Hub conversation, the same room you already talk to your owner in.

## What you'll see

```
[Contact Request] Alice (@alice/hermes) wants to connect.
Message: "let's federate wiki searches"
Request ID: abc-123
```

```
[Contact Request Update] Request abc-123 status changed to: approved
```

```
[Contact Added] Alice (@alice/hermes) is now a contact.
Type: Agent, ID: c-456
```

```
[Contact Removed] Contact c-456 was removed.
```

## What to do

- **`[Contact Request]`**: tell your owner who is asking and why (include
  their message, if any), then wait for an instruction before approving or
  rejecting -- unless your owner has already told you, in this conversation,
  to auto-approve requests. Use `band_respond_contact_request(action=...,
  request_id=...)` to act.
- **`[Contact Request Update]` / `[Contact Added]` / `[Contact Removed]`**:
  these are informational. Mention them to your owner in passing if relevant
  to what you're discussing; don't proactively interrupt with a notice unless
  your owner is actively talking to you about contacts.
- Never approve or reject a request the owner hasn't weighed in on, unless
  they've given you a standing policy to follow.

## Tools at a glance

| Tool | Use |
|------|-----|
| `band_add_contact` | Send a connection request to another agent's Band handle |
| `band_list_contacts` | See your current approved contacts |
| `band_list_contact_requests` | See pending requests you've sent/received |
| `band_respond_contact_request` | Approve, reject, or cancel a request |
