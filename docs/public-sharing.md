# Explicit room sharing

Rooms stay private when saved. From **Share**, a signed-in owner selects a saved
Original, Improved, Chat refinement, or Your layout version, reviews its rendered
preview, enters a public title and optional caption, and explicitly opts in.

Explore and `#shared/<public-id>` expose only that independent snapshot: catalog
geometry, colors, title, caption, and a PNG rendered from the scene. Photos, chat,
clarification answers, observation IDs, audit notes, account identity, and private
project identifiers are excluded. Public browsing is anonymous and read-only.

Private edits and saves do not update a publication. **Update sharing** replaces
it only with fresh consent. **Unpublish** revokes the public JSON and preview on
their next request while preserving the private room. Deleting the saved room
also revokes its publication. Republishing creates a new public URL. Previously
downloaded content cannot be recalled.

Apply migration `20261027_05` with `alembic upgrade head`. Publishing uses the
existing database and CPU scene renderer; it makes no model calls and consumes no
AI session or action allowance. Concurrent changes use project/publication
revision checks, and the feed uses cursor pagination (12 rooms per page).
