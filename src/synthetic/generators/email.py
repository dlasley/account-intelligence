# ruff: noqa: E501
"""Email signal generator.

Produces an InboundPayload-compatible dict (see src/pipeline/normalizer.py:27-55).

Contract rules enforced here (ADR-015 §D10):
- `from_email` always matches ^[^@]+@[^@]+\\.[^@]+$
- `body` is always non-empty after strip()
- No `routing_method`, `routing_confidence`, or `account_id` fields in the output
- Accepts a seeded `random.Random` instance — no module-level random calls
- Accepts a `now: datetime` parameter — no datetime.now() calls
"""

import random
import uuid
from datetime import datetime

from src.synthetic.scenario import AxesSpec, SignalSpec

# ---------------------------------------------------------------------------
# Sentence-template corpus
# 5 registers x ~12-15 templates each = ~65 templates total.
# Templates use {contact_name}, {account_name}, {topic} as placeholders.
# "chain" register provides a quoted-reply block appended to paragraph bodies.
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, list[str]] = {
    "formal": [
        "Dear {contact_name}, I am writing on behalf of {account_name} to discuss {topic}. Please advise on the appropriate next steps.",
        "To whom it may concern at {account_name}: we wish to formally inquire about {topic} and request a meeting at your earliest convenience.",
        "This message serves as official correspondence from {account_name} regarding {topic}. We require a written response within five business days.",
        "Please be advised that {account_name} has reviewed the proposed terms concerning {topic} and has several questions prior to execution.",
        "I am reaching out on behalf of {account_name}'s procurement team to request clarification on {topic}. Your prompt reply is appreciated.",
        "Following the recent discussion, {account_name} wishes to confirm in writing our understanding of {topic} as outlined below.",
        "As representatives of {account_name}, we are writing to formally document our position regarding {topic} for your records.",
        "We respectfully request that your team review the attached summary of {topic} and provide a response at {account_name}'s earliest opportunity.",
        "The management of {account_name} has authorized me to contact you regarding {topic} and to outline our expectations going forward.",
        "On behalf of {account_name}, I would like to acknowledge receipt of your previous communication and address the matter of {topic} directly.",
        "I write to you in a formal capacity representing {account_name} and wish to raise the issue of {topic} for consideration.",
        "This correspondence is intended to initiate a formal review process at {account_name} with respect to {topic}.",
    ],
    "technical": [
        "Hi {contact_name}, we ran into an issue with {topic} on {account_name}'s deployment — seeing 403s on the export endpoint. Any idea if this is a known bug?",
        "Hey, quick question about {topic}: we're using the bulk API at {account_name} and seeing occasional timeouts above 5k rows. Is there a page-size cap?",
        "Following up on {topic}: the webhook payloads {account_name} is receiving are missing the `account_id` field in about 10% of events. Can you confirm the schema?",
        "We've integrated {topic} into {account_name}'s CI pipeline and the latency is spiking to 8s p99. Worth looking at the connection pool settings?",
        "The {topic} feature seems to behave differently in sandbox vs. prod on {account_name}'s account — specifically around the retry logic. Can we debug together?",
        "Heads up on {topic}: we updated {account_name}'s config last Thursday and now the reports are showing stale data. Reverting doesn't fix it.",
        "Quick API question about {topic}: does the endpoint at {account_name} support cursor-based pagination, or only offset? The docs are unclear.",
        "We noticed {topic} is caching aggressively on {account_name}'s plan — cache TTL appears to be 24h but we need near-real-time. Is there a flag to disable it?",
        "For {topic} at {account_name}: the SSO integration dropped after the IdP migration. Seems like the assertion format changed. Can you re-validate?",
        "Investigating {topic} on {account_name}'s side — the diff between expected and actual output is pasted below. Suspect a serialization issue.",
        "Re: {topic} — {account_name} is hitting the rate limit at 900 req/min even though the contract says 1200. Is the counter per-IP or per-API-key?",
        "We're setting up {topic} for {account_name}'s staging environment and the environment variable docs don't match actual behavior. Can you clarify?",
        "The {topic} dashboard at {account_name} is showing data from the wrong date range after DST rollover. Looks like a UTC offset issue in the query.",
    ],
    "casual": [
        "Hey {contact_name}! Quick note about {topic} — things at {account_name} have been going really well since we turned it on.",
        "Just wanted to loop you in on {topic}. The team at {account_name} loves it — big win for us this quarter.",
        "Hey! Checking in on {topic}. We're all excited about what {account_name} has been doing with it lately.",
        "Wanted to share some good news about {topic}: {account_name} hit a milestone last week and your product was a big part of it.",
        "Hi {contact_name}, hope you're well! Pinging about {topic} — nothing urgent, just wanted to stay in touch and see if there's anything new.",
        "Quick update from {account_name}: {topic} is working great. Team is happy, no complaints.",
        "Hey, saw your blog post about {topic} and immediately thought of {account_name}'s use case. Would love to explore it.",
        "Hi! Just a heads-up that {account_name} is going through a reorg but {topic} is still a priority for us.",
        "Following up from our last chat about {topic} — haven't forgotten, just been a bit heads-down at {account_name}.",
        "Hey {contact_name}, all good on {topic}. {account_name} is growing fast and we'll probably need to revisit the plan soon.",
        "Short one: {account_name} loved the last demo on {topic}. Can we set up a follow-up?",
        "Just FYI — {topic} rollout at {account_name} went smoothly. Thanks for the support last week.",
    ],
    "escalation": [
        "This is the third time I'm raising {topic} with your team and {account_name} has not received an acceptable resolution. We need to escalate.",
        "I need to be direct: the ongoing issues with {topic} are impacting {account_name}'s operations. We expect a response from leadership within 24 hours.",
        "Given the lack of progress on {topic}, {account_name} is now formally requesting a call with your VP of Support by end of week.",
        "I've documented {topic} in three previous tickets and this is the fourth contact from {account_name}. The delay is no longer acceptable.",
        "{account_name} is evaluating whether to continue the contract due to unresolved issues with {topic}. We need immediate escalation.",
        "The situation with {topic} has deteriorated significantly. {account_name}'s leadership is now involved and expects an executive-level response.",
        "This is an urgent escalation. {account_name} experienced a production outage related to {topic} and the root cause has still not been provided.",
        "We are formally putting {account_name}'s account on probationary review due to repeated failures around {topic}. Please respond urgently.",
        "Your team's handling of {topic} has fallen below the SLA agreed with {account_name}. We are requesting a formal incident report by COB today.",
        "I want to be clear: if {topic} is not resolved within 48 hours, {account_name} will be exercising the contract termination clause.",
        "{account_name} has lost confidence in the current support track for {topic}. We require executive sponsorship on a resolution path.",
        "This is not the experience {account_name} was promised. The persistent issues with {topic} need immediate, senior attention.",
    ],
    "apologetic": [
        "Hi {contact_name}, I wanted to apologize for the delay in following up on {topic} from {account_name}'s side — we had internal blockers.",
        "Sorry for the slow response on {topic}. {account_name} went through a budget cycle and things stalled. We're back on track now.",
        "Apologies for the confusion on {topic}. Our team at {account_name} miscommunicated internally — here's the correct status.",
        "I owe you an update on {topic}. {account_name} dropped the ball on the last review cycle and I want to set the record straight.",
        "Thanks for your patience on {topic}. We at {account_name} have been slower than expected getting alignment, but we're committed.",
        "My apologies for the missed meeting last week about {topic}. {account_name} had an urgent incident and I should have communicated sooner.",
        "I'm sorry to hear about the experience with {topic}. On behalf of {account_name}, I'd like to make this right and start fresh.",
        "This is long overdue — apologies for going dark on {topic}. {account_name} was heads-down on a product launch and lost track.",
        "Sorry for the short notice, but {account_name} needs to reschedule the {topic} review. I take full responsibility for the timing.",
        "I realize {account_name} hasn't been the most responsive partner on {topic}. I'm committed to changing that going forward.",
        "Apologies for the miscommunication around {topic}. {account_name}'s team was not aligned, and the confusion landed on your side unfairly.",
        "I should have flagged {topic} earlier. {account_name} has been slow to act and I want to acknowledge that directly.",
    ],
}

# ---------------------------------------------------------------------------
# Topical template families — ADR-015 Rev 1 / Rev 2.
# 7 topics x 5 registers x 6 templates = 210 templates.
# Each family covers: formal, technical, casual, escalation, apologetic registers.
# Dispatch: when axes.concern_topic != "none", _TOPICAL_TEMPLATES[topic][register]
# is used in place of _TEMPLATES[register].
# ---------------------------------------------------------------------------

_TOPICAL_TEMPLATES: dict[str, dict[str, list[str]]] = {
    "pricing": {
        "formal": [
            "Dear {contact_name}, {account_name} wishes to formally review the pricing structure for {topic}. We request a detailed cost breakdown at your earliest convenience.",
            "On behalf of {account_name}, I am writing to discuss the financial terms associated with {topic} and to request a meeting with your commercial team.",
            "Following our recent review, {account_name} has concerns regarding the pricing model for {topic} and seeks clarification before the upcoming renewal.",
            "I am writing to formally request a revised quote for {topic} on behalf of {account_name}. The current pricing does not align with our budget expectations.",
            "{account_name} is currently evaluating all vendor contracts including {topic}. We require a formal pricing proposal by end of quarter.",
            "Please consider this a formal notification that {account_name} intends to renegotiate the pricing terms for {topic} prior to the next renewal cycle.",
        ],
        "technical": [
            "Hi {contact_name}, we're trying to model total cost of ownership for {topic} at {account_name} and the pricing page doesn't cover overage rates clearly.",
            "Quick question on {topic} pricing: does {account_name}'s current tier include API call volume, or is that billed separately at overage rates?",
            "We're building a budget projection at {account_name} and need to understand how {topic} scales — is pricing per seat, per API call, or flat?",
            "For {topic}: {account_name} is at ~80% of the usage cap this month. What's the per-unit overage rate if we exceed the plan limit?",
            "Looking at {topic} cost modelling for {account_name}. Can you share the volume discount schedule for customers above 10k events/day?",
            "We noticed a line item change on the {account_name} invoice related to {topic}. Can you walk me through what changed and why?",
        ],
        "casual": [
            "Hey {contact_name}! Circling back on {topic} — our finance team at {account_name} has some questions about the pricing before we sign off.",
            "Quick one on {topic}: {account_name} is trying to get budget approved and our CFO wants to understand the cost model better. Any help?",
            "Hi! On {topic} — we love the product but {account_name}'s budget team is pushing back on cost. Is there any flexibility?",
            "Hey, just want to flag that {account_name} got a price increase notice on {topic} and it caught us off guard. Can we talk?",
            "Following up on {topic} — {account_name} is trying to right-size the plan. What's the cheapest tier that still gets us what we need?",
            "Hi {contact_name}, hope all is well! {account_name} is in budget season and {topic} is on the list to review. Worth a quick chat?",
        ],
        "escalation": [
            "This is an escalation regarding {topic}. {account_name} received an unexpected invoice increase and we have not received an adequate explanation.",
            "I need to be direct: the pricing change for {topic} was not communicated to {account_name} in advance and we consider this a breach of our agreement.",
            "{account_name} is formally disputing the invoice for {topic}. The charges exceed the contracted rate and we expect a credit within 5 business days.",
            "Given the unresolved pricing dispute on {topic}, {account_name} is withholding payment until we receive a corrected invoice and written explanation.",
            "The undisclosed price increase on {topic} has escalated to {account_name}'s VP of Finance. We require an executive-level response by end of week.",
            "I am escalating {topic} to your commercial leadership. {account_name} was not notified of the rate change and we are evaluating contract options.",
        ],
        "apologetic": [
            "Hi {contact_name}, I apologize for the delay in responding to the {topic} pricing discussion. {account_name} has been in internal budget cycles.",
            "Sorry for the slow turnaround on {topic}. {account_name}'s finance team needed additional approvals and the process took longer than expected.",
            "I owe you a follow-up on {topic} pricing. {account_name} had to pause the conversation during a reorganization but we're ready to move forward.",
            "Apologies for the confusion around {topic} costs. {account_name}'s internal alignment took longer than expected — here's where we stand now.",
            "I should have flagged {topic} pricing concerns earlier. {account_name} let this sit too long and I want to reset the conversation.",
            "Sorry for the gap in communication on {topic}. {account_name} went through a procurement process change and your quote got delayed.",
        ],
    },
    "outage": {
        "formal": [
            "Dear {contact_name}, {account_name} is formally documenting the service disruption affecting {topic}. We require a post-incident report within 48 hours.",
            "On behalf of {account_name}, I am writing to request a root cause analysis for the recent outage impacting {topic} and affected service levels.",
            "This is official correspondence from {account_name} regarding the {topic} service interruption. We request SLA credit and a written remediation plan.",
            "{account_name} formally requests an incident debrief meeting to review the {topic} outage timeline, impact, and prevention measures.",
            "Please treat this as a formal notice that the {topic} outage impacted {account_name}'s operations. We expect a written response from your incident team.",
            "I am writing on behalf of {account_name} to formally request compensation for business disruption caused by the {topic} service degradation.",
        ],
        "technical": [
            "Hi {contact_name}, {account_name} is seeing complete failure on {topic} — all requests timing out since ~14:00 UTC. Is there a known incident?",
            "Urgent: {topic} is down for {account_name}. Getting 503s on all endpoints. Status page shows green but we're clearly impacted. Any updates?",
            "{account_name} has been experiencing intermittent failures on {topic} for the past 6 hours. Error logs show connection resets on the load balancer.",
            "Following up on the {topic} incident from yesterday — {account_name} is still seeing elevated error rates (p99 latency 8x normal). Not fully recovered.",
            "The {topic} outage impacted {account_name}'s nightly batch job. We need to understand if data was lost or if we can safely re-run the pipeline.",
            "Can you share the {topic} incident timeline? {account_name} needs it for our own internal post-mortem and SLA reporting.",
        ],
        "casual": [
            "Hey {contact_name}! Heads up — {topic} looks down at {account_name}'s end. Anything going on? Status page looks fine but we're definitely seeing issues.",
            "Hi! Quick flag — {account_name} is having trouble with {topic} right now. The team is blocked. Anything you can share on ETA to fix?",
            "Hey, not sure if you're aware but {topic} has been flaky for {account_name} since this morning. Any idea what's going on?",
            "Quick note: {account_name}'s users are hitting errors on {topic}. Probably an outage on your side? Would love a heads up when it's resolved.",
            "Hi {contact_name}, {topic} went down for us about an hour ago. {account_name}'s team is pretty frustrated. What's the status?",
            "Hey — just checking in on {topic}. {account_name} saw some instability earlier today and want to make sure everything is back to normal.",
        ],
        "escalation": [
            "This is an urgent escalation. {account_name} has experienced a production outage on {topic} for over 4 hours and has received no update from support.",
            "I need to escalate the {topic} incident immediately. {account_name} is fully blocked and the lack of communication from your team is unacceptable.",
            "Given the extended {topic} downtime, {account_name} is now engaging our executive sponsor. We expect a VP-level response within the hour.",
            "The {topic} outage has now cost {account_name} measurable business impact. We are documenting damages and will pursue SLA credits at minimum.",
            "{account_name} is formally putting your team on notice regarding the {topic} incident. Response time is well outside contracted SLA.",
            "This is the third outage affecting {account_name} related to {topic} this quarter. We require an executive business review and a reliability roadmap.",
        ],
        "apologetic": [
            "Hi {contact_name}, I apologize for the delayed response during the {topic} outage. {account_name} was scrambling internally and should have communicated faster.",
            "Sorry for the confusion we caused around {topic}. {account_name}'s team sent conflicting reports to your support team and I want to clarify the situation.",
            "I owe you an apology regarding the {topic} incident ticket. {account_name} closed it prematurely — the issue has recurred and I should have kept it open.",
            "Apologies for the noise on {topic}. {account_name} was testing in production (I know) and some of the error reports were on our end.",
            "I should have been clearer in my earlier report about {topic}. {account_name}'s symptoms were related to a misconfiguration we introduced.",
            "Sorry for the back-and-forth on {topic}. {account_name} had conflicting data and I want to give you the accurate picture now.",
        ],
    },
    "feature_gap": {
        "formal": [
            "Dear {contact_name}, {account_name} wishes to formally submit a feature request for {topic}. This capability is required for our compliance workflow.",
            "On behalf of {account_name}, I am writing to document our requirement for {topic} functionality currently missing from your platform.",
            "{account_name} formally requests a roadmap commitment for {topic}. Without this feature, our ability to expand usage is limited.",
            "Please treat this as an official product feedback submission from {account_name} regarding the absence of {topic} in the current release.",
            "I am writing on behalf of {account_name} to request a timeline for {topic}. This is a blocker for our planned platform expansion.",
            "{account_name} has identified {topic} as a critical missing capability. We request a meeting with your product team to discuss prioritization.",
        ],
        "technical": [
            "Hi {contact_name}, we need {topic} at {account_name} — specifically the ability to filter by custom metadata fields in the API response. Is this on the roadmap?",
            "Quick question: does {topic} support bulk operations at {account_name}'s scale? We're talking 50k records per batch and the current API doesn't seem to handle it.",
            "We're blocked at {account_name} because {topic} doesn't support webhook retries with exponential backoff. Any plans to add this?",
            "The {topic} API at {account_name}'s integration point doesn't expose the `account_status` field we need. Is there a workaround or timeline for the fix?",
            "For {topic}: {account_name} needs read-only API access with scoped permissions. The current all-or-nothing key model is a security issue for us.",
            "We've hit a wall with {topic} — {account_name} needs multi-region data residency and the current architecture doesn't support it. What's the plan?",
        ],
        "casual": [
            "Hey {contact_name}! Quick ask on {topic} — {account_name}'s team has been looking for a way to export data in CSV directly. Any chance that's coming?",
            "Hi! We love the product but {account_name} keeps running into the same wall with {topic}. Is there a workaround or should we just wait for the feature?",
            "Hey, is {topic} on the roadmap for this year? {account_name} keeps working around the gap and it'd be great to know if we should build internally or wait.",
            "Quick one: {account_name} would really benefit from {topic} having dark mode. Minor, but the design team keeps asking. Any plans?",
            "Hi {contact_name}, {account_name} loves using the product! One thing we keep wishing for is better {topic} support. Anything in the pipeline?",
            "Hey — any update on {topic}? {account_name} asked about this a few months ago and just wondering if it's moved up in priority.",
        ],
        "escalation": [
            "This is a formal escalation. The absence of {topic} is blocking {account_name}'s expansion plans and we need a committed roadmap date.",
            "I need to be direct: {account_name} made the purchase decision based on an expectation that {topic} would be available. It is not, and we need answers.",
            "The missing {topic} functionality has caused {account_name} to build expensive workarounds. We need a timeline commitment or a discussion about contract adjustments.",
            "{account_name} is escalating the {topic} feature gap to your CPO. This has been outstanding for over a year and we are losing confidence in the roadmap.",
            "I need an executive-level response on {topic}. {account_name}'s board has asked why this capability doesn't exist and I don't have a good answer.",
            "Given the unresolved {topic} gap, {account_name} is evaluating alternative vendors. We need a commitment this week or we will initiate an RFP.",
        ],
        "apologetic": [
            "Hi {contact_name}, I apologize for the pressure we've put on the {topic} request. {account_name} understands roadmap tradeoffs and I should have framed it better.",
            "Sorry for being persistent about {topic}. {account_name}'s use case is genuinely blocked but I realize I've been in your inbox too often.",
            "I owe you an apology on {topic}. {account_name} submitted the feature request through the wrong channel and it may have gotten lost.",
            "Apologies for the confusion on {topic}. {account_name}'s team was testing a workaround and our feedback may have been misleading.",
            "I should have been clearer about {account_name}'s {topic} requirement upfront. It's on us for not scoping it properly during onboarding.",
            "Sorry for the back-and-forth on {topic}. {account_name} keeps changing the requirements on our side — here's the consolidated ask.",
        ],
    },
    "utilization_decline": {
        "formal": [
            "Dear {contact_name}, {account_name} wishes to formally discuss declining utilization of {topic} and explore options to improve adoption.",
            "On behalf of {account_name}, I am writing to request a usage review meeting for {topic}. Our adoption metrics suggest training gaps exist.",
            "This correspondence formally notifies you that {account_name}'s utilization of {topic} has declined and we would like to explore remediation.",
            "{account_name} requests a formal account review for {topic} to understand utilization patterns and develop a reengagement plan.",
            "Please consider this a formal request from {account_name} for access to our {topic} usage analytics to support an internal adoption initiative.",
            "I am writing on behalf of {account_name} to request a structured enablement program for {topic} to address observed utilization gaps.",
        ],
        "technical": [
            "Hi {contact_name}, {account_name}'s {topic} usage has dropped 40% over the last 30 days. Can you pull the detailed activity logs so we can investigate?",
            "We're seeing a sharp drop in {topic} API calls at {account_name}. The event counts dropped from ~1200/day to ~300/day two weeks ago. Any ideas?",
            "For {topic}: {account_name}'s active user count is down from 85 to 23 since last quarter. Is there a way to see which features they were using before?",
            "The {topic} dashboard at {account_name} is barely being used. Can we get a report on which modules have zero activity in the last 60 days?",
            "Quick question on {topic} telemetry: {account_name} suspects a specific team stopped using the feature after an update. Can you share session data?",
            "Investigating the {topic} utilization drop at {account_name}. The pattern suggests the change happened right after the v2.3 update — is that a known regression?",
        ],
        "casual": [
            "Hey {contact_name}! Honest check-in on {topic} — {account_name}'s team hasn't been using it much lately. Any ideas for getting them re-engaged?",
            "Hi! Quick note on {topic}: usage at {account_name} has been pretty low this quarter. Might be a training thing or just a busy period. Any suggestions?",
            "Hey, noticed {account_name} hasn't been using {topic} as much. Is there anything new we should know about that might make it more useful for us?",
            "Following up on {topic}: {account_name}'s adoption has been slower than expected. The team is enthusiastic about the product but keeps defaulting to old tools.",
            "Hi {contact_name}, worth a chat on {topic}? {account_name} signed up for it but honestly adoption has been a struggle. Looking for tips.",
            "Hey — {topic} just isn't sticking at {account_name}. Not a product problem per se, more of a change management issue on our side. Any advice?",
        ],
        "escalation": [
            "This is an escalation. {account_name} is paying for {topic} and utilization is near zero. We need an immediate adoption plan or a discussion about contract value.",
            "I need to be direct: {account_name} has invested significantly in {topic} and we are not seeing ROI. This requires an executive-level conversation.",
            "{account_name} is formally requesting a business review for {topic}. Utilization has been declining for two consecutive quarters with no intervention.",
            "The lack of utilization support for {topic} is a failure of your customer success model. {account_name} expects a remediation plan within 5 days.",
            "Given zero utilization of {topic} despite multiple requests for enablement, {account_name} is exploring contract exit options.",
            "{account_name} escalates the {topic} adoption failure. We have requested help three times with no actionable outcome.",
        ],
        "apologetic": [
            "Hi {contact_name}, I apologize for {account_name}'s slow adoption of {topic}. Internal change management has been harder than we anticipated.",
            "Sorry for the poor utilization on {topic}. {account_name} went through a leadership change and the initiative lost sponsorship temporarily.",
            "I owe you an update on {topic} usage. {account_name} has been slow to roll it out internally — we dropped the ball on the enablement plan.",
            "Apologies for not being more transparent about {account_name}'s {topic} usage struggles. We should have asked for help sooner.",
            "I should have flagged the {topic} adoption issues at {account_name} earlier. We tried to solve it internally and let it drag on too long.",
            "Sorry for the disappointing utilization numbers on {topic}. {account_name} is committed to improvement but we clearly need support.",
        ],
    },
    "competitive": {
        "formal": [
            "Dear {contact_name}, {account_name} is formally conducting a vendor evaluation that includes {topic} as a comparison point. We request a capabilities review.",
            "On behalf of {account_name}, I am writing to inform you that we are evaluating alternatives to {topic} as part of our annual vendor review.",
            "{account_name} has received a compelling proposal from a competing vendor on {topic}. We request a formal response and revised commercial terms.",
            "Please be advised that {account_name} is conducting a formal RFP process for {topic}. We are inviting your participation as an incumbent vendor.",
            "I am writing on behalf of {account_name} to request a competitive review meeting for {topic}. We have received pricing from three alternative providers.",
            "{account_name} formally requests a business case briefing for {topic} to help our evaluation committee compare solutions.",
        ],
        "technical": [
            "Hi {contact_name}, {account_name} is evaluating {topic} alternatives — specifically Vendor X's approach to data residency. How do you compare?",
            "Quick competitive question: a rival of yours demoed {topic} with native Salesforce integration last week. Does your platform support that natively at {account_name}'s scale?",
            "We're being pitched on {topic} alternatives that claim 3x faster data sync. {account_name} needs to understand if the performance gap is real.",
            "The alternative vendor's {topic} API supports GraphQL. Is that on your roadmap? {account_name} uses GraphQL internally and the REST-only model is a friction point.",
            "For {topic}: a competitor is offering {account_name} a 90-day POC. To be fair in our evaluation, can we get access to your equivalent trial environment?",
            "{account_name} has been comparing {topic} benchmark numbers. The competing platform shows lower p99 latency — can you walk us through your architecture differences?",
        ],
        "casual": [
            "Hey {contact_name}! Not trying to be awkward but {account_name} is being actively pitched on {topic} alternatives. Anything you want us to know before we evaluate?",
            "Hi! Quick heads up — {account_name} got a cold outreach from your competitor on {topic}. Just FYI. Nothing decided, but wanted you to know.",
            "Hey, honest check-in: {account_name} is looking at alternatives for {topic}. Mostly curious if there's anything new on your end we might be missing.",
            "Hi {contact_name}, {account_name} is in a budget review and {topic} is being compared against some cheaper alternatives. Can we talk about value?",
            "Quick note: {account_name} has a demo with a {topic} competitor next week. I'd love to hear your side before we go in.",
            "Hey — {account_name} is being pulled toward a different vendor for {topic}. Happy to share what we've heard if it's useful for you to know.",
        ],
        "escalation": [
            "This is a formal notice: {account_name} is actively evaluating replacing {topic} with a competing solution. We expect a response from leadership this week.",
            "I need to be direct: {account_name} received a better offer for {topic} from your direct competitor. We need a counter-proposal within 48 hours.",
            "{account_name} is escalating the competitive review for {topic} to your executive team. The incumbent advantage you have is being seriously reconsidered.",
            "The competing vendor's {topic} offer is materially better on price and features. {account_name} will switch unless we see a credible retention proposal.",
            "Given the competitive pressure on {topic}, {account_name} expects an executive sponsor response. A CSM-level conversation will not be sufficient.",
            "I am formally notifying you that {account_name} has received a signed proposal from a {topic} competitor. You have 5 business days to respond.",
        ],
        "apologetic": [
            "Hi {contact_name}, I apologize for using the threat of competition to push on {topic}. {account_name} should have raised our concerns directly.",
            "Sorry for the noise around the {topic} evaluation at {account_name}. We should have talked to you first before going to market.",
            "I owe you transparency on {topic}: {account_name} has been exploring alternatives without telling you. That's not fair and I want to reset.",
            "Apologies for the abrupt {topic} evaluation notice. {account_name}'s procurement team kicked off the process without coordinating with our team.",
            "I should have flagged {account_name}'s {topic} concerns before getting into a competitive process. I'm sorry for how this played out.",
            "Sorry for the way the {topic} evaluation unfolded. {account_name} values the relationship and wants to find a path forward together.",
        ],
    },
    "success_expansion": {
        "formal": [
            "Dear {contact_name}, {account_name} wishes to formally discuss expansion of {topic} to additional business units. We are ready to proceed.",
            "On behalf of {account_name}, I am writing to initiate a formal expansion discussion for {topic}. Our pilot results have exceeded expectations.",
            "{account_name} formally requests a proposal for expanding {topic} to cover our international subsidiaries. We anticipate a 3x increase in seat count.",
            "Please treat this as a formal intent to expand {account_name}'s {topic} deployment. We would like to begin the commercial process.",
            "I am writing on behalf of {account_name} to document our satisfaction with {topic} and to initiate the expansion process.",
            "{account_name} formally recognises the value delivered by {topic} and requests an executive briefing to plan the next phase of deployment.",
        ],
        "technical": [
            "Hi {contact_name}, {account_name} wants to roll out {topic} to three additional teams. Can we discuss API rate limit increases ahead of the volume jump?",
            "We're expanding {topic} at {account_name} and need to understand the multi-tenant architecture. We'll be onboarding two subsidiaries with separate data boundaries.",
            "For {topic}: {account_name} is ready to move from the pilot to full production. What's the migration path for the ~50k historical records?",
            "The {topic} integration at {account_name} is working great. We want to extend it to our partner portal — does the API support external OAuth flows?",
            "We're planning to automate {topic} at {account_name} via the API. Can you confirm the rate limits and whether webhooks fire for batch operations?",
            "Quick pre-expansion question on {topic}: {account_name} will double daily event volume after rollout. Should we expect any infrastructure changes on your side?",
        ],
        "casual": [
            "Hey {contact_name}! Great news — {account_name} loves {topic} and we want to roll it out to two more teams. Can we set up a call to plan it out?",
            "Hi! Just wanted to share that {topic} has been a huge hit at {account_name}. The team keeps asking for more. Let's talk expansion!",
            "Hey, following up on {topic} — {account_name} hit a major milestone last week and your product was a big part of it. Time to grow!",
            "Quick win to share: {account_name}'s {topic} usage is up 200% this quarter and leadership wants to expand. Exciting times!",
            "Hi {contact_name}, {account_name} is ready to bring {topic} to our whole org. Been waiting for the right moment and it's now!",
            "Hey — {account_name} just got internal approval to expand {topic} significantly. Really excited about what we can do together from here.",
        ],
        "escalation": [
            "This is an urgent escalation — in a positive direction. {account_name}'s demand for {topic} is outpacing your onboarding capacity. We need resources now.",
            "I need to escalate the {topic} expansion timeline at {account_name}. Leadership has committed to a Q3 rollout and we cannot wait for standard queues.",
            "{account_name} is ready to triple the {topic} deployment but your team has been slow to process the expansion order. We need executive attention.",
            "The {topic} expansion at {account_name} is being blocked by contract processing delays on your side. We have board commitment and cannot miss the window.",
            "I am escalating {account_name}'s {topic} expansion to your VP of Customer Success. The opportunity is significant and the execution friction is unacceptable.",
            "Given {account_name}'s strategic commitment to {topic}, the 6-week onboarding delay is not acceptable. We need an accelerated path.",
        ],
        "apologetic": [
            "Hi {contact_name}, I apologize for the slow movement on {account_name}'s {topic} expansion. Internal approvals took longer than planned.",
            "Sorry for the mixed signals on the {topic} expansion. {account_name}'s leadership kept changing scope and that landed on your team unfairly.",
            "I owe you an update on {account_name}'s {topic} rollout plans. We've been quiet but we're actually ready to move — sorry for the silence.",
            "Apologies for the delay in formalizing {account_name}'s {topic} expansion. Procurement cycles were longer than expected but we're clear now.",
            "I should have kept you updated on {account_name}'s {topic} expansion timeline. We went quiet during an internal restructuring.",
            "Sorry for the back-and-forth on {topic} scope. {account_name} has finalized the plan and I want to reset with a clear ask.",
        ],
    },
    "renewal_pending": {
        "formal": [
            "Dear {contact_name}, {account_name} is in the formal renewal evaluation phase for {topic}. We request a comprehensive value review meeting.",
            "On behalf of {account_name}, I am writing to initiate the renewal process for {topic}. We have a number of commercial and technical questions to address.",
            "{account_name} formally acknowledges receipt of the {topic} renewal notice. We will require an updated proposal reflecting current usage levels.",
            "Please be advised that {account_name}'s {topic} contract expires in 60 days. We request a renewal discussion with your commercial team.",
            "I am writing on behalf of {account_name} to confirm that we are in active renewal discussions for {topic} and to document outstanding questions.",
            "{account_name} formally requests a final renewal proposal for {topic} that reflects our expanded usage and the competitive landscape.",
        ],
        "technical": [
            "Hi {contact_name}, as we approach the {account_name} renewal for {topic}, can you pull our actual usage stats vs entitlement? We want to right-size.",
            "For the {topic} renewal at {account_name}: we've added two new API integrations since signing. Does the new contract scope cover those use cases?",
            "Quick question ahead of {account_name}'s {topic} renewal: has the SLA changed? We want to compare it against what we negotiated last year.",
            "We're reviewing {topic} before {account_name}'s renewal and have questions about the new data export format. Is the old format still supported?",
            "For the {topic} renewal: {account_name} wants to move from annual to monthly billing. Does your platform support that for our tier?",
            "Ahead of the {account_name} {topic} renewal, can we get a technical roadmap briefing? Helps our team justify the renewal internally.",
        ],
        "casual": [
            "Hey {contact_name}! {account_name}'s {topic} renewal is coming up and I wanted to get ahead of it. Let's set up a quick call?",
            "Hi! Quick heads up — {account_name} is starting to look at the {topic} renewal. We're happy with the product but finance wants to do a value check.",
            "Hey, renewal season for {topic} is coming and {account_name} would love to chat before we just click 'renew'. Anything new we should know about?",
            "Hi {contact_name}, we're 90 days from {account_name}'s {topic} renewal. Happy to move forward but want to make sure pricing is still right.",
            "Quick one: {account_name}'s {topic} renewal is on our Q3 list. Can we block some time to review what we've gotten out of it this year?",
            "Hey — just flagging that {account_name}'s {topic} contract renews in two months. Let's make sure it's set up for the next phase of our growth.",
        ],
        "escalation": [
            "This is an escalation. {account_name}'s {topic} renewal is in 30 days and we have not received a revised proposal despite three requests.",
            "I need to escalate the {topic} renewal timeline at {account_name}. Finance has a hard deadline and your team's response time is jeopardizing it.",
            "{account_name} is escalating the {topic} renewal to your VP of Sales. The commercial terms we've been offered do not reflect our 3-year relationship.",
            "Given the impasse on {account_name}'s {topic} renewal terms, we are requesting an executive call this week. Failure to resolve may result in non-renewal.",
            "I am formally notifying you that {account_name} will not auto-renew {topic} unless we receive a competitive proposal by end of week.",
            "The {topic} renewal process for {account_name} has been mishandled. We've had three different reps and no continuity. Escalating now.",
        ],
        "apologetic": [
            "Hi {contact_name}, I apologize for the late start on {account_name}'s {topic} renewal. Our procurement team lost track of the timeline.",
            "Sorry for the radio silence on {topic} renewal. {account_name} went through a leadership transition and the process got delayed.",
            "I owe you an apology on the {topic} renewal process. {account_name} should have engaged earlier and made your team scramble at the last minute.",
            "Apologies for the confusion on {account_name}'s {topic} renewal requirements. We've been changing specs internally and that's not fair to you.",
            "I should have started the {topic} renewal conversation earlier on {account_name}'s side. I'm sorry for the compressed timeline.",
            "Sorry for the slow movement on {topic} renewal. {account_name} had a budget freeze that delayed everything and we should have communicated sooner.",
        ],
    },
}

# Quoted-reply block appended to "chain" bodies (simulates email threading)
_CHAIN_SUFFIX = (
    "\n\n--- Original message ---\n"
    "From: {prior_sender}@{prior_domain}\n"
    "Subject: Re: {topic}\n\n"
    "Thanks for the update. We'll follow up shortly.\n"
)

# Domains used when contact_email_origin != "corporate"
_FREE_MAIL_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"]

# Topics varied per signal — provides content diversity across signals for the same account
_TOPICS = [
    "API rate limits",
    "SSO configuration",
    "export functionality",
    "onboarding progress",
    "renewal terms",
    "support ticket response",
    "data retention policy",
    "feature roadmap",
    "billing questions",
    "integration setup",
    "performance issues",
    "user provisioning",
    "contract renewal",
    "executive business review",
    "dashboard access",
    "data migration",
    "webhook configuration",
    "reporting accuracy",
    "compliance requirements",
    "upgrade timeline",
]

# Contact first names for variety
_FIRST_NAMES = [
    "Alex",
    "Jordan",
    "Morgan",
    "Taylor",
    "Casey",
    "Riley",
    "Drew",
    "Cameron",
    "Quinn",
    "Avery",
    "Blake",
    "Reese",
    "Skylar",
    "Dana",
    "Kendall",
]
_LAST_NAMES = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Wilson",
    "Anderson",
    "Thomas",
    "Jackson",
    "White",
    "Harris",
]


def build_contact_pool(
    rng: random.Random,
    axes: AxesSpec,
    primary_domain: str,
) -> list[tuple[str, str]]:
    """Build the fixed contact pool for an entire SignalSpec.

    Call once per spec in the orchestrator; pass the result to every signal in
    that spec so contact_diversity is honored across signals, not just within one.

    Returns [(email, name), ...]:
      single  → exactly 1 contact
      multi   → 2-3 contacts
      crowded → 4-6 contacts
    Domain governed by contact_email_origin:
      corporate      → @<primary_domain>
      personal_email → @<free_mail_domain>
      mixed          → ~60% corporate, 40% free-mail
    """
    count_map = {"single": 1, "multi": rng.randint(2, 3), "crowded": rng.randint(4, 6)}
    count = count_map.get(axes.contact_diversity, 1)

    pool: list[tuple[str, str]] = []
    for _ in range(count):
        first = rng.choice(_FIRST_NAMES)
        last = rng.choice(_LAST_NAMES)
        name = f"{first} {last}"

        if axes.contact_email_origin == "corporate":
            domain = primary_domain
        elif axes.contact_email_origin == "personal_email":
            domain = rng.choice(_FREE_MAIL_DOMAINS)
        else:  # "mixed"
            domain = primary_domain if rng.random() < 0.6 else rng.choice(_FREE_MAIL_DOMAINS)

        local = f"{first.lower()}.{last.lower()}"
        email = f"{local}@{domain}"
        pool.append((email, name))

    return pool


def _pick_contacts(
    rng: random.Random,
    contact_pool: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Select contacts for a single signal from the pre-built pool.

    For a single-element pool the same sender is always returned.
    For multi/crowded pools a sender is chosen at random from the pool, with
    the remaining pool members used as CC recipients.
    """
    if len(contact_pool) == 1:
        return list(contact_pool)
    # Pick one sender at random; the rest become CC recipients
    sender_idx = rng.randrange(len(contact_pool))
    result = [contact_pool[sender_idx]]
    result.extend(c for i, c in enumerate(contact_pool) if i != sender_idx)
    return result


def _resolve_email_tone(rng: random.Random, axes: AxesSpec, signal_index_within_spec: int) -> str:
    """Map sentiment_trajectory + email_tone to a concrete tone string for this signal.

    sentiment_trajectory governs how tone drifts across signals:
      flat              → same tone throughout
      declining         → starts technical/casual, ends escalation
      recovering        → starts escalation/apologetic, ends casual/technical
      oscillating       → alternates between positive and negative tones
      sudden_escalation → switches to escalation at the midpoint

    email_tone is the base tone when trajectory is flat.
    """
    traj = axes.sentiment_trajectory
    base = axes.email_tone
    i = signal_index_within_spec

    if traj == "flat":
        return base
    elif traj == "declining":
        # 0-33%: technical, 33-66%: apologetic, 66-100%: escalation
        if i < 4:
            return "technical"
        elif i < 8:
            return "apologetic"
        else:
            return "escalation"
    elif traj == "recovering":
        # 0-33%: escalation, 33-66%: apologetic, 66-100%: technical
        if i < 4:
            return "escalation"
        elif i < 8:
            return "apologetic"
        else:
            return "technical"
    elif traj == "oscillating":
        return "casual" if i % 2 == 0 else "escalation"
    elif traj == "sudden_escalation":
        # First half: formal/technical; second half: escalation
        return "escalation" if i >= 6 else "formal"
    return base


def _build_body(
    rng: random.Random,
    axes: AxesSpec,
    contact_name: str,
    account_name: str,
    topic: str,
    register: str,
    primary_domain: str,
) -> str:
    """Construct an email body string.

    message_length governs body size:
      short      → one sentence (≤80 chars target)
      paragraph  → one template (80-400 chars)
      multi      → two templates concatenated (400-1200 chars)
      chain      → paragraph + quoted-reply block

    Always returns non-empty string (ADR-015 §D10).
    """
    # Dispatch to topical family when concern_topic is set; fall back to default templates.
    concern_topic = getattr(axes, "concern_topic", "none")
    if concern_topic != "none" and concern_topic in _TOPICAL_TEMPLATES:
        topic_family = _TOPICAL_TEMPLATES[concern_topic]
        templates = topic_family.get(register, topic_family.get("casual", next(iter(topic_family.values()))))
    else:
        templates = _TEMPLATES.get(register, _TEMPLATES["technical"])
    template = rng.choice(templates)
    body = template.format(
        contact_name=contact_name,
        account_name=account_name,
        topic=topic,
    )

    if axes.message_length == "short":
        # Truncate to first sentence, min 20 chars
        sentence_end = body.find(".")
        if sentence_end > 20:
            body = body[: sentence_end + 1]
        # If still too long, just trim
        if len(body) > 100:
            body = body[:80]
    elif axes.message_length == "multi":
        second_template = rng.choice(templates)
        second_body = second_template.format(
            contact_name=contact_name,
            account_name=account_name,
            topic=topic,
        )
        body = f"{body}\n\n{second_body}"
    elif axes.message_length == "chain":
        prior_first = rng.choice(_FIRST_NAMES)
        prior_last = rng.choice(_LAST_NAMES)
        prior_domain = primary_domain
        suffix = _CHAIN_SUFFIX.format(
            prior_sender=f"{prior_first.lower()}.{prior_last.lower()}",
            prior_domain=prior_domain,
            topic=topic,
        )
        body = f"{body}{suffix}"

    # Guarantee non-blank (defensive — templates always non-empty, but be explicit)
    if not body.strip():
        body = f"Message from {account_name} regarding {topic}."

    return body


def generate_email_payload(
    spec: SignalSpec,
    rng: random.Random,
    now: datetime,
    signal_index: int,
    scenario_name: str,
    account_name: str,
    primary_domain: str,
    signal_index_within_spec: int = 0,
    contact_pool: list[tuple[str, str]] | None = None,
) -> dict:
    """Generate a single InboundPayload-compatible dict.

    Args:
        spec: The SignalSpec driving this signal's axes.
        rng: Seeded Random instance — no module-level random calls.
        now: Timestamp for this signal — no datetime.now() calls.
        signal_index: Zero-based index in the full generated sequence; used for uuid5.
        scenario_name: Used to derive the deterministic external_id.
        account_name: Human-readable account name for template substitution.
        primary_domain: Primary domain for corporate email addresses.
        signal_index_within_spec: Position within this SignalSpec's count; drives register drift.
        contact_pool: Pre-built pool of (email, name) tuples for this spec.
            When provided, contacts are selected from this fixed pool so that
            contact_diversity is honored across all signals in the spec, not just
            within a single signal.  The orchestrator builds this once per spec.
            When None (legacy / test callers), a pool is built on the fly (one signal).

    Returns:
        dict matching InboundPayload schema — ready for json.dumps() into RawInboundEvent.raw_payload.
    """
    axes = spec.axes

    # --- Contacts ---
    if contact_pool is None:
        # Legacy path: build a single-signal pool from axes (preserves backward compat
        # for callers that don't supply a pool, e.g. isolated unit tests).
        contact_pool = build_contact_pool(rng, axes, primary_domain)
    contacts = _pick_contacts(rng, contact_pool)
    from_email, from_name = contacts[0]
    to_emails = [e for e, _ in contacts[1:]]

    # --- Topic ---
    topic = rng.choice(_TOPICS)

    # --- Tone (respects sentiment trajectory) ---
    register = _resolve_email_tone(rng, axes, signal_index_within_spec)

    # --- Body ---
    body = _build_body(
        rng,
        axes,
        contact_name=from_name.split()[0],
        account_name=account_name,
        topic=topic,
        register=register,
        primary_domain=primary_domain,
    )

    # Apply any overrides from the spec (field-level post-generation overrides)
    subject_base = f"Re: {topic}" if signal_index_within_spec > 0 else topic.capitalize()

    # --- Thread ID (threading_topology governs thread continuity) ---
    base_thread_id = f"thread-{scenario_name}-{spec.account_slug}"
    topology = axes.threading_topology
    if topology == "linear":
        thread_id: str | None = base_thread_id
    elif topology == "branching":
        # Two threads: even-indexed signals on thread A, odd on thread B
        thread_id = f"{base_thread_id}-{'a' if signal_index_within_spec % 2 == 0 else 'b'}"
    elif topology == "standalone":
        # Each signal gets its own thread ID (no threading)
        thread_id = f"{base_thread_id}-{signal_index}"
    else:  # "missing_thread_id"
        thread_id = None

    # --- Deterministic external_id: uuid5(NAMESPACE_DNS, "{scenario}:{signal_index}") ---
    external_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{scenario_name}:{signal_index}"))

    payload = {
        "external_id": external_id,
        "source_type": "json_fixture",  # process_event path matches fixture flow
        "direction": "inbound",
        "channel": "email",
        "occurred_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "from_email": from_email,
        "from_name": from_name,
        "to_emails": to_emails,
        "subject": subject_base,
        "thread_id": thread_id,
        "body": body,
        "in_reply_to": None,
        "references": None,
        "metadata": None,
    }

    # Apply spec-level overrides last
    for key, value in spec.overrides.items():
        payload[key] = value

    return payload
