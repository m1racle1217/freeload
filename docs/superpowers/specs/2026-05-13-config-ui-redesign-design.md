# Freeload WebUI Config Redesign

Date: 2026-05-13
Status: Approved in conversation, pending written-spec review

## Goal

Redesign the WebUI so it feels more polished, supports system theme following with manual light/dark override, and allows users to edit the application configuration directly in the browser without touching `config/config.yaml`.

The result should feel like a compact operations console rather than a raw settings dump. It should preserve the existing FastAPI + Jinja structure and avoid introducing a frontend framework.

## Scope

In scope:

- Refresh the shared WebUI visual system
- Add theme support: `system`, `light`, `dark`
- Persist theme choice in the browser
- Replace the read-only config page with a fully form-based config editor
- Save validated changes back to `config/config.yaml`
- Handle sensitive config values without echoing secrets back to the browser
- Show environment-variable override state clearly

Out of scope:

- Live daemon hot-reload without restart
- Multi-user settings or authentication
- Replacing the current server-rendered stack with SPA tooling
- Editing arbitrary YAML keys outside the known config schema

## Product Direction

The approved direction is a mixed style:

- Structure from the current dashboard/admin style: grouped navigation, dense but readable layout
- Visual tone from the darker console concept: deeper sidebar, more grounded contrast, more professional than playful
- Form treatment from the softer card concept: clear fields, calm borders, friendlier inputs, less raw-machine feel

This should read as "operations workbench" rather than "developer debug page".

## Information Architecture

### Shared shell

Keep the current left navigation model, but restyle it into a more coherent shell:

- Persistent sidebar for desktop
- Compact mobile navigation treatment
- Shared page header area with title, description, and page-level actions
- Global theme switcher in the header

### Config page structure

The config page becomes a three-column workbench on desktop:

1. Left section rail
2. Main form surface
3. Right contextual help and status rail

On smaller screens, this collapses into a single-column layout in this order:

1. Page header
2. Section selector
3. Form content
4. Status/help blocks
5. Action bar

### Config sections

The form is organized into stable sections derived from the existing config schema:

- `notify.email`
- `platforms`
- `web`
- `browser`

Each section should expose only meaningful fields and use controls matched to field type.

## UI Design

### Theme system

The app supports three display modes:

- `system`: follows `prefers-color-scheme`
- `light`: always light
- `dark`: always dark

Implementation direction:

- Use CSS custom properties in the base template
- Apply theme through a root `data-theme` attribute
- Resolve the effective theme in a small inline script before paint to reduce flashing
- Save preference in `localStorage`

The palette should preserve the app's operational feel, with:

- Deep neutral sidebar
- Clear accent color for active states and primary actions
- Distinct surface levels for cards, form panels, and helper blocks
- Contrast-safe colors in both light and dark themes

### Typography and spacing

Preserve the current compact admin density, but improve hierarchy:

- Clear page title and short supporting copy
- Consistent card and panel rhythm
- Field labels stronger than helper text
- Tables and logs remain dense, but with better surface separation and state styling

### Config editing controls

Map config values to form controls rather than freeform text:

- Booleans: switches
- Numeric values: number inputs with min/max/step
- Enum-like values: selects if future schema introduces them
- Email values: email inputs
- Host and generic strings: text inputs
- Platform settings: repeated grouped rows/cards per platform

The config UI should not expose raw YAML editing in the main workflow.

## Data Model and Form Mapping

The backend should treat config editing as editing a known schema, not arbitrary nested JSON.

### Canonical editable shape

The form model should mirror the existing configuration:

```text
notify.email.smtp_host
notify.email.smtp_port
notify.email.use_ssl
notify.email.from_addr
notify.email.password
notify.email.to_addr

platforms.<platform>.enabled
platforms.<platform>.poll_interval
platforms.<platform>.value_threshold

web.host
web.port

browser.pool_size
browser.headless
```

### Sensitive fields

`notify.email.password` needs special handling:

- The server must never send the real stored password back to the browser
- The form should display this field as empty or masked placeholder state
- If the user leaves it blank on save, preserve the existing stored value
- If the user enters a new value, overwrite the stored value

## Backend Design

### Config service responsibilities

Extend the config layer so it can support both read and write flows cleanly:

- Read raw YAML from disk
- Return a UI-safe view model
- Accept validated updates from the WebUI
- Merge updates with existing config where secret preservation is needed
- Write YAML back to disk
- Reload in-memory config after save

Recommended additions in `src/config.py`:

- A method to return a form-safe config payload
- A method to validate and normalize submitted form data
- A method to save updated config to `config/config.yaml`

The existing `to_dict()` masking behavior is useful for preview, but the config editor should use a dedicated form payload rather than a generic masked dump.

### Web routes and APIs

Add or refactor routes in [server.py](/E:/PythonProject/freeload/src/web/server.py):

- `GET /config`
  Renders the redesigned config page with the safe config payload.

- `GET /api/config`
  Returns the form-safe config model plus metadata like environment overrides.

- `POST /api/config`
  Accepts submitted config values, validates them, saves YAML, reloads config, and returns success or field errors.

Optional but useful:

- `POST /api/config/test-email`
  Verifies SMTP settings before saving or after editing.

If implemented, the email test should be isolated and clearly report failure cause without mutating saved config unless explicitly desired.

### Environment overrides

The current system lets environment variables override email settings. The UI should make that explicit.

For each affected field:

- Show whether a runtime environment variable override exists
- Indicate that the saved YAML value may not be the effective runtime value

The config save flow should still write YAML normally. The UI is informative here; it does not edit environment variables.

## Interaction Design

### Save flow

1. User edits fields
2. Form tracks dirty state
3. Save button becomes active
4. Client performs lightweight validation
5. Server performs authoritative validation
6. On success:
   show a success state and remind the user that daemon restart is required for full effect
7. On failure:
   show field-level errors and preserve unsaved inputs

### Reset flow

The page should support resetting unsaved changes back to the last loaded values without a full browser reload.

### Validation behavior

Validation should be close to fields and human-readable.

Required checks:

- Email fields are syntactically valid where applicable
- Ports are numeric and in range
- Poll intervals are positive integers
- Value thresholds are non-negative numbers
- Pool size is a positive integer

### Restart messaging

Because config changes are not automatically applied everywhere today, the success state should say that a daemon restart is required for guaranteed effect.

This message belongs in the UI rather than being silently assumed.

## Frontend Structure

### Template changes

Update [base.html](/E:/PythonProject/freeload/src/web/templates/base.html) to host:

- Theme variables
- Theme toggle UI
- Shared form/button/table states
- Better responsive shell behavior

Replace [config.html](/E:/PythonProject/freeload/src/web/templates/config.html) with a structured editor page:

- Page header
- Section rail
- Config forms
- Validation surfaces
- Action bar
- Context/help column

The other templates should inherit the refreshed shell styling so the redesign feels coherent across the app, even if only the config page gets major interaction changes in the first pass.

## Error Handling

Handle these failure cases explicitly:

- Config file missing
- Config file malformed YAML
- Save write failure
- Server-side validation failure
- Email test failure

For each case:

- Return a user-facing error message
- Avoid partial writes
- Keep the user's current form values visible when possible

Config writes should be done carefully:

- Validate first
- Serialize second
- Write atomically if practical

## Testing

### Backend tests

Add tests around config editing behavior:

- Safe payload does not expose real password
- Blank password submission preserves existing password
- Non-blank password submission replaces existing password
- Validation rejects invalid ports, intervals, thresholds, and malformed email addresses
- Saving writes expected YAML structure

### Web/API tests

If existing test coverage remains light, at minimum add route-level tests for:

- `GET /api/config`
- `POST /api/config` success
- `POST /api/config` validation failure

### Manual verification

Before completion, verify:

- Theme follows system mode
- Manual theme override persists after refresh
- Config values render correctly
- Save updates `config/config.yaml`
- Password preservation works
- Environment override hints display correctly
- Mobile layout remains usable

## Implementation Notes

Keep the implementation conservative:

- Stay within FastAPI + Jinja + vanilla JS
- Prefer a small amount of focused client script over large abstractions
- Centralize schema and validation rules in Python
- Avoid duplicating config field definitions across multiple layers more than necessary

## Risks and Mitigations

Risk: form schema drifts from YAML schema.
Mitigation: centralize editable field definitions and validation in the config layer.

Risk: secret handling accidentally clears stored credentials.
Mitigation: treat blank password as "preserve existing value", with explicit tests.

Risk: theme changes create inconsistent styles across templates.
Mitigation: move colors and shared surface styles into the base template first.

Risk: users think saving instantly changes running daemon behavior.
Mitigation: show restart guidance in the success UI.

## Recommended Implementation Sequence

1. Refactor shared shell and theme tokens in `base.html`
2. Add config read/write helpers and validation in `src/config.py`
3. Add config APIs in `src/web/server.py`
4. Rebuild `config.html` as a form-based editor
5. Refresh related page styling to match the new shell
6. Add tests for config save and secret handling
7. Run manual browser verification
