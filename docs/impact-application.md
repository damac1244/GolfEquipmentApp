# Impact application — Bagdrop

Everything here is true as of **5 October 2026**. The figures come from the
live price file; if you apply later, re-check them — overstating is the fastest
way to be removed after approval, and reviewers do look.

**Apply at:** app.impact.com → sign up as a **Partner** (not a Brand)

---

## The short description

*(For "Describe your business" / "About your property", usually 100–200 words.)*

> Bagdrop is a golf club price comparison site at thebagdropgolf.com. It tracks
> what every seller it can reach charges for the same club, matches listings
> that describe that club in different ways, and ranks them by the total price
> including shipping.
>
> It currently follows 962 clubs across 176 sellers, with prices refreshed
> daily and every listing time-stamped on the page so a shopper can see how
> recently each price was confirmed.
>
> The matching engine normalises loft, flex, dexterity, shaft and set
> composition, so a "TaylorMade P790 Irons 4-PW Stiff" from one seller and a
> "P790 Iron Set, Right-Handed, 4-PW, Dynamic Gold Stiff" from another are
> recognised as one club and shown as one row with both prices. Fittings that
> differ — a 9° stiff against a 12° ladies — are kept apart, because quoting
> one price for both would be misleading.

---

## Promotional methods

*(The field that sinks most applications. Be specific; vague answers read as
link-dumping.)*

> Outbound links appear only inside price comparison results, attached to a
> specific club the visitor has searched for. Someone looking at a TaylorMade
> P790 iron set sees every seller we have a price for, ordered by total cost,
> and clicks through to whichever they choose.
>
> Three things follow from that model:
>
> **Traffic is high-intent.** Visitors arrive having already chosen a club and
> are comparing where to buy it. They are at the end of the purchase decision,
> not the beginning.
>
> **Ranking is by price alone.** We do not reorder results by commission. A
> merchant appears above another because their total price including shipping
> is lower, and for no other reason. This is stated publicly in the site
> footer.
>
> **No coupon or incentive traffic.** We do not operate a discount-code site,
> toolbar, browser extension or cashback scheme, and we do not bid on brand
> terms in paid search.

---

## Audience and traffic

*(Be honest. This is the section people lie on and it is the easiest to check.)*

> The site is live and functional but new, and we are not yet driving
> meaningful traffic. We are not claiming otherwise.
>
> What exists today is the infrastructure: a working comparison tool serving
> 962 clubs from 176 sellers, an automated daily refresh, and a matching
> engine that has been through several rounds of correction. We are seeking
> merchant partnerships now because product feeds are the input the comparison
> runs on — not because we have an audience to monetise yet.

---

## The merchants to request

Impact is per-merchant: joining the network is not the same as being approved
by a brand. Apply to these, in this order.

**1. GlobalGolf — the priority.** Already the third-largest source of listings
in our data (262 listings directly, plus 243 through their Walmart storefront).
They are also the largest used and pre-owned catalogue we can reach, and used
gear is the half of the site we currently cannot serve properly.

**2. PGA TOUR Superstore** — 235 listings already in our data.

**3. Callaway Pre-Owned** — 169 listings, and the same used-inventory argument
as GlobalGolf.

**4. TaylorMade** — 169 listings, brand-direct, and TaylorMade is the most
represented brand in our catalogue.

**Worth saying in the free-text box on each merchant application:**

> Your listings already appear in our price comparison, sourced from public
> shopping results. We would rather send that traffic through a proper
> affiliate relationship — with accurate product links, correct attribution
> and a feed that carries condition and specification as real fields — than
> through generic search links that serve neither of us well.

That is true, specific, and gives them a concrete reason to approve you.

---

## Practical notes

**Property type:** Comparison shopping / price comparison. Not "blog", not
"coupon".

**Categories:** Sports & Outdoors → Golf.

**Don't leave free-text boxes empty.** A short specific answer beats a blank
field; blanks read as a bot.

**Expect per-merchant decisions.** Network approval is step one. Each brand
decides separately and some take a fortnight.

**If declined, ask why.** Impact usually tells you, and it is often something
fixable — a disclosure that isn't prominent enough, or a site that looks
unfinished.

---

## After approval — the wiring

The adapter is written and registered (`app/adapters/impact.py`). Three things
have to happen before a single Impact price reaches the site, and none of them
are automatic.

**1. Add the two secrets.** Repo → Settings → Secrets and variables → Actions:

- `IMPACT_ACCOUNT_SID`
- `IMPACT_AUTH_TOKEN`

**2. Pass them to the workflow.** In `.github/workflows/refresh-prices.yml`,
the `Fetch prices` step has an `env:` block that currently lists only the
Serper keys. Add:

```yaml
          IMPACT_ACCOUNT_SID: ${{ secrets.IMPACT_ACCOUNT_SID }}
          IMPACT_AUTH_TOKEN: ${{ secrets.IMPACT_AUTH_TOKEN }}
```

**3. Stop pinning one source.** The same step ends with:

```
          python -m app.ingest --source shopping
```

Change it to:

```
          python -m app.ingest
```

Without `--source`, the ingest runs every source that has credentials — so
shopping keeps working and Impact joins it. With `--source shopping` left in
place, the Impact credentials are simply ignored and nothing changes, which is
a confusing failure to debug.

**Check the feed shape on the first run.** Impact has migrated its Catalogs
endpoints more than once, so field names may not match what the adapter
expects. Run this locally before trusting a scheduled run:

```
python -m app.ingest --probe impact
```

It prints the first raw item and how we parsed it. If the fields don't line up,
that output is what's needed to fix the adapter — it is far easier than reading
the run logs after a failed refresh.

---

## Then Awin, for 2nd Swing

**2nd Swing is already the single largest source of listings in the data — 610
of them.** They are the biggest dedicated used-club specialist in the business
and they run their programme through Awin rather than Impact, so it is a
separate application with the same copy, lightly edited.

Between GlobalGolf and 2nd Swing you would have the two deepest used
catalogues in golf. That matters more than the commission rate: **used
listings are currently 2% of the site's data**, while the homepage headline is
"Someone else already broke it in." Those two facts need to stop contradicting
each other, and a merchant feed is the only thing that fixes it.

---

## What this is actually worth

Worth being clear-eyed about the order of events. Approval gives you working
product links and a feed with real condition and specification fields — that
is the fix for the site's biggest quality problems, and it is worth doing for
that reason alone.

Revenue is a separate question and comes later. Commission requires traffic,
and the site has very little yet. Getting approved does not start the money; it
removes the reason the money is currently impossible.
