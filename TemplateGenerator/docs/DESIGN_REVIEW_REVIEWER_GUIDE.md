# Design review: reviewer guide

Audience: design reviewers. For other roles, use the
[documentation index](README.md).

## Open the review

1. Open the reviewer URL supplied by the operator, for example
   `http://<team-host>:8765/`.
2. Enter the **reviewer** access code. The operator code is different.
3. Enter your name and stable email or team ID.
4. Select **Load my votes** to restore anything you already saved in the
   current review round.

## Review each design

For every active design:

1. Inspect the reference design.
2. If **View generated review** is enabled, inspect the art-template and
   transformed-pet contact sheets. The gallery shows the first complete product
   profile in alphabetical profile-ID order.
3. Choose one recommendation:
   - **Graduate**: strong candidate for bundle development.
   - **Improve**: promising, but another design iteration is needed.
   - **Abandon**: should not advance in its current direction.
4. Add a concise note explaining the decision or requested improvement.
5. Use the filters when useful:
   - **Collection** uses the collection recorded for the design.
   - **Title** searches both the visible title and design ID.
   - **Bottom line** searches the slogan independently.
   - **My vote** filters by your current saved or unsaved recommendation.
   - **Current selection** shows designs with or without a recommendation.
6. Continue reviewing. Changed cards display **Unsaved**, and the sticky review
   bar shows the total number of unsaved changes even when those cards are
   hidden by a filter.

When ready, select **Save all changes** once. The entire changed set is saved
atomically: either every changed vote is stored or none is. Confirm that the
review bar reports the saved count and the changed cards display **Saved**.
`Command-S` on macOS or `Ctrl-S` on other platforms performs the same action.
If a feedback note was entered without a recommendation, the page clears its
filters, identifies the incomplete card, and does not save a partial batch.

Filters do not discard changes. Selecting a recommendation can immediately hide
a card when it no longer matches **My vote** or **Current selection**; the
change remains included in **Save all changes**. Use **Clear filters** to see
the complete active list again.

You may revise and save your votes until the operator closes or resets the
round. Saving again updates existing votes; it does not create duplicates.
The browser warns before leaving while changes remain unsaved. This warning is
not durable draft storage, so save before closing the page.

## What changes between rounds

When an improved design starts a new review round, its prior choice is no longer
selected and the progress counter treats it as unreviewed. Evaluate the updated
design again rather than assuming the old feedback still applies.

Abandoned and graduated designs disappear from the reviewer gallery. If an
operator restores one, it returns to the active list. Refresh the page or use
**Load my votes** after the operator announces a lifecycle update.

Reviewers cannot access rankings, other reviewers' feedback, exports, or design
lifecycle actions. Contact the operator rather than sharing the operator URL or
credentials.
