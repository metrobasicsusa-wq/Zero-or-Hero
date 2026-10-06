# EP first-party review — group 3

All 17 cases attempted. Pass 8; unknown 9; definitive candidate failures 0.

Categories and request windows follow the frozen protocol. An identified outside-category story does not establish absence of other news, so that candidate remains unknown. Current-source timestamp evidence is not a historical archive. No exit or profit/loss outcomes were inspected.

| Candidate | Gate | Event | Time UTC |
|---|---|---|---|
| 2026-05-11__WYFI | unknown | not established | unresolved |
| 2026-05-12__AMBQ | pass | earnings | 2026-05-12T11:30:00Z |
| 2026-05-12__WEN | unknown | unconfirmed_take_private_report | unresolved |
| 2026-05-13__VELO | pass | earnings | 2026-05-12T20:05:00Z |
| 2026-05-20__IMVT | pass | earnings | 2026-05-20T11:00:00Z |
| 2026-05-21__INFQ | unknown | proposed_government_funding_letter_of_intent | 2026-05-21T10:49:00Z |
| 2026-05-21__RGTI | unknown | proposed_government_funding_letter_of_intent | 2026-05-21T11:15:49Z |
| 2026-05-26__MNTS | unknown | employee_inducement_equity_awards | 2026-05-22T21:00:27Z |
| 2026-05-27__MNTS | pass | contract | 2026-05-27T12:01:01Z |
| 2026-05-28__SNOW | pass | earnings | 2026-05-27T20:05:00Z |
| 2026-05-28__UMAC | unknown | reported_funding_talks | unresolved |
| 2026-05-29__OKTA | unknown | earnings | unresolved |
| 2026-05-29__REPL | pass | regulatory announcement | 2026-05-29T12:00:48Z |
| 2026-06-01__SPCE | unknown | operational_flight_test_update_outside_window | 2026-05-27T20:15:00Z |
| 2026-06-08__ABAT | pass | regulatory announcement | 2026-06-08T09:51:04Z |
| 2026-06-17__QURE | pass | regulatory announcement | 2026-06-17T11:05:23Z |
| 2026-07-10__VOD | unknown | contract | unresolved |

## 2026-05-11__WYFI

Only a multi-stock premarket mover mention was available in the input. Bounded searches and issuer investor-news landing page did not yield an identifiable in-window primary announcement. The current issuer page is dynamically populated and provided no historical release content; this is missing evidence, not proof of no catalyst.


## 2026-05-12__AMBQ

Issuer-issued original BusinessWire release reports first-quarter results and second-quarter guidance. Its published and modified time is 11:30 UTC. Issuer Q4 JSON-LD instead labels 06:30 UTC, a five-hour conflict; both timestamps are strictly within the window and the later time is used. This does not establish universal Q4 timezone semantics.

- [AMBQ-ir](https://ir.ambiq.com/news/news-details/2026/Ambiq-Reports-First-Quarter-2026-Financial-Results/default.aspx); SHA-256 `f4f96cddfa4fa9254a36a6bdbbad2131873a02b58ebea81a33c5165ae67c8088`. Q4 JSON-LD, conflicts with original wire; both in window.
- [AMBQ-wire](https://www.businesswire.com/news/home/20260512580745/en/); SHA-256 `52121a98af47f801bcf758548db06b494b8c19a97b59529a4387446ff5ae664e`. original issuer-issued BusinessWire JSON-LD.

## 2026-05-12__WEN

Supplied stories describe press reports that an investor was seeking financing for a potential take-private bid. No executed agreement or issuer confirmation in the required window was found in the bounded source attempts. A rumor does not satisfy the registered contract category; absence of another qualifying story is not established.


## 2026-05-13__VELO

Issuer IR and issuer-issued PR Newswire release report first-quarter financial results and reaffirm annual revenue guidance. The original wire declares publication at 16:05:00 EDT and modification at 16:05:31 EDT; the later instant is used and both are after the prior regular close.

- [VELO-ir](https://ir.velo3d.com/news-events/press-releases/detail/206/velo3d-announces-first-quarter-2026-financial-results); SHA-256 `d5bf92237208dd7e8e37657104b910e04e0400e167e3af84acb93c9e450fb155`. visible 4:05 pm EDT and datetime.
- [VELO-wire](https://www.prnewswire.com/news-releases/velo3d-announces-first-quarter-2026-financial-results-302770009.html); SHA-256 `2335a87d4d20739a30480a5bba7462c4674716762a6605d3c49bdf0338b0e3b1`. original issuer-issued PR Newswire JSON-LD.

## 2026-05-20__IMVT

Issuer IR displays May 20 at 07:00 EDT and includes fourth-quarter and fiscal-year financial results. The same release includes clinical results, but the earnings content independently satisfies the frozen category. No drug approval is asserted.

- [IMVT-ir](https://www.immunovant.com/investors/news-events/press-releases/detail/83/immunovant-provides-corporate-updates-and-reports-financial); SHA-256 `39aa2ac40bf89a507346fd00417360529a4cec39aa3e7e27c09898aee85fd08f`. visible May 20, 2026 7:00 am EDT.

## 2026-05-21__INFQ

Issuer confirms a signed LOI at 06:49 EDT, but explicitly states proposed funding remains contingent on diligence, definitive award documents and government internal approvals. The timing is established; whether this preliminary LOI meets the frozen contract category is unresolved. It is not recorded as a definitive grant or funded award.

- [INFQ-ir](https://ir.infleqtion.com/news-events/press-releases/detail/188/infleqtion-signs-letter-of-intent-with-the-u-s-department-of-commerce-for-100-million-to-accelerate-u-s-leadership-in-quantum-computing); SHA-256 `3be93bc6a590a6424352cf7d99f0f5f4bcf0a6d4521909a9578ac309743ad834`. visible May 21, 2026 6:49 am EDT.

## 2026-05-21__RGTI

Issuer confirms a signed LOI for potential government funding, with datePublished 07:15:49 EDT. Its release describes definitive transaction agreements and related transaction conditions as prospective. This preliminary LOI is not silently promoted to a definitive funded contract; category remains unresolved.

- [RGTI-ir](https://investors.rigetti.com/news-releases/news-release-details/rigetti-signs-letter-intent-us-government-quantum-computing); SHA-256 `4d99b0431e0eeb5da539156823182a4569972303199061c1a5a81c6b6873949b`. issuer JSON-LD timezone -0400.

## 2026-05-26__MNTS

The issuer archive identifies an in-window release granting equity inducement awards to six employees. This is compensation governance, not earnings, guidance, a new business contract, or a completed regulatory communication under the frozen categories. The supplied mover story supplies no identified alternative catalyst, whose absence is unproven.

- [MNTS26-awards](https://investors.momentus.space/news-releases/news-release-details/momentus-grants-inducement-awards-six-new-employees); SHA-256 `ecb6506130432208df77aaaf1c74767fcbb664dfd5d6dc67d28d6381e30cdf47`. issuer JSON-LD timezone -0400.

## 2026-05-27__MNTS

Issuer says it entered into securities purchase agreements for a private placement and declares publication at 08:01:01 EDT. An executed agreement satisfies the frozen contract category without imposing a new revenue requirement. This is financing with potential dilution, not a customer order, realized proceeds or a claim of favorable news; closing remained conditional.

- [MNTS27-placement](https://investors.momentus.space/news-releases/news-release-details/momentus-announces-pricing-25-million-private-placement-common); SHA-256 `e40c663d1f3463b6acd6207d63d0ed488a693613cde2497605c306f41e424927`. issuer JSON-LD timezone -0400.

## 2026-05-28__SNOW

Issuer-issued original BusinessWire release reports first-quarter fiscal 2027 financial results. Published and modified JSON-LD timestamps both equal 20:05 UTC, strictly after the prior close and before the candidate open. Other same-day stories are not needed to classify this earnings announcement.

- [SNOW-wire](https://www.businesswire.com/news/home/20260527027931/en/Snowflake-Reports-Financial-Results-for-the-First-Quarter-of-Fiscal-2027); SHA-256 `52d76c540fc971bd7703dcbe5d315bd8e87609c32828bf93114da6fa5e2a599b`. original issuer-issued BusinessWire JSON-LD.

## 2026-05-28__UMAC

The input identifies a press report about government funding talks. Bounded search and issuer press-release landing page did not establish an in-window signed agreement or completed government announcement. Ongoing talks alone are outside the frozen categories; the dynamically populated issuer page is not a complete historical archive.


## 2026-05-29__OKTA

The earnings announcement is genuine, but issuer IR JSON-LD labels publication and modification 15:01 UTC on May 28, before the prior regular close, while issuer-issued original BusinessWire labels both 20:01 UTC. A five-hour CMS timezone error is plausible but unproven. Since interpretations cross the strict window boundary, the candidate remains unknown; original wire availability at 20:01 alone does not prove first publication was after the close.

- [OKTA-ir](https://investor.okta.com/news-and-events/news-releases/news-details/2026/Okta-Announces-First-Quarter-Fiscal-Year-2027-Financial-Results/default.aspx); SHA-256 `b98f93fad42626dd42f0494320222eeb6e171e67d5c7696776278890e7d15cb3`. Q4 JSON-LD conflicts across prior-close boundary.
- [OKTA-wire](https://www.businesswire.com/news/home/20260528809289/en/); SHA-256 `5e3a195c8d9bc84cc041a8b2be2dba24aa3025f7c02f3dd409fdbb8a4ae295ad`. original issuer-issued BusinessWire JSON-LD.

## 2026-05-29__REPL

Issuer publication reports completed FDA communications and agreement on a path for BLA resubmission and reconsideration, with datePublished 08:00:48 EDT. This is a completed regulatory communication, not approval of RP1 and not proof that a resubmission had already occurred.

- [REPL-announcement](https://ir.replimune.com/news-releases/news-release-details/replimune-announces-planned-rp1-bla-resubmission-following); SHA-256 `8c64af76dd56dcded571d69676bc2df2212b5b3868ea4d12c0779dbfbb7a0dbc`. issuer JSON-LD timezone -0400.

## 2026-06-01__SPCE

The identified issuer-issued original wire concerns a glide-flight operational update, dated May 27 at 20:15 UTC, before the candidate window beginning May 29 at 20:00 UTC. Weekend retrospective/mover mentions cannot refresh that original event into a new in-window catalyst. Bounded search does not exclude another undiscovered qualifying announcement.

- [SPCE-wire](https://www.businesswire.com/news/home/20260527635284/en/VSS-Unity-Returns-to-the-Skies-In-Preparation-For-New-Spaceship-Flight-Test-Program); SHA-256 `cfd6e5b5ff0ebdbab758b1bf78de136b36b850b4256281b05a5848199f8f2518`. original issuer-issued BusinessWire JSON-LD.

## 2026-06-08__ABAT

Issuer-issued original GlobeNewswire release declares publication and modification at 09:51:04 UTC and reports that DOE reinstated a previously terminated grant following appeal. This is a completed government decision. The $115 million describes total project cost; it must not be mislabeled as the grant amount.

- [ABAT-wire](https://www.globenewswire.com/news-release/2026/06/08/3307883/0/en/American-Battery-Technology-Company-Wins-Appeal-and-Has-US-Department-of-Energy-Grant-Reinstated-for-115-million-Project-for-Commercial-Scale-Critical-Mineral-Lithium-Refinery.html); SHA-256 `c6fe4f47e5877106f6c49d6ce82399e8616f50f2df633fdf80e24220de0d6ab5`. original issuer-issued GlobeNewswire JSON-LD and time element.

## 2026-06-17__QURE

Issuer IR declares 07:05:23 EDT and reports a completed Type B FDA meeting in which the agency communicated that three-year trial data could form the primary basis of an accelerated-approval BLA. Planned submission and possible accelerated approval remain prospective; this is regulatory communication, not marketing approval.

- [QURE-ir](https://uniqure.gcs-web.com/news-releases/news-release-details/uniqure-announces-plan-bla-submission-amt-130-huntingtons); SHA-256 `fb1595833dfb45849feacc6771b4aa8a6143b78af6cb6bb858ea4f897f4bd396`. issuer JSON-LD timezone -0400.

## 2026-07-10__VOD

e& confirms signing a binding agreement to sell its Vodafone stake, and Vodafone acknowledges the announcement. Retrieved official pages and the official announcement PDF provide July 10, 2026 but no sufficiently precise publication time. Third-party article times cannot establish first-party preopen availability; exact-window evidence remains unresolved.

- [VOD-ir](https://www.eand.com/en/news/10-07-26-eand-vodafone-investment-sale.html); SHA-256 `cda4e6b09cd6f2d88fd778f2e14db3240135b79d5bcbe13ee209565fbf03d994`. visible date July 10, 2026 only.
- [VOD-pdf](https://www.eand.com/content/dam/eand/assets/docs/latest-announcements/2026/adx-announcement-board-resolution-en-10-07-26.pdf); SHA-256 `fc5b682959fca273025554542929fbc83be3c286cef9703c1793e03eb01237de`. official announcement document date only.
- [VOD-response](https://www.vodafone.com/news/newsroom/corporate-and-financial/response-to-announcement); SHA-256 `a095be777c04c502cd5a35098cad3f4152a2174b4686be36e2ba251bbc0291b6`. visible date July 10, 2026 only.
