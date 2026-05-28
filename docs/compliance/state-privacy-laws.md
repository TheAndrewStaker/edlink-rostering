# State student privacy laws

State-level privacy frameworks that layer on top of FERPA and COPPA. **This is the single largest hidden cost in K-12 EdTech expansion.** Each new state requires fresh review.

Federal frameworks set the floor; states often go stricter. Some states have substantially more EdTech-specific provisions than federal law.

This doc is a state-by-state catalog. For each state where the application has customers, do a fresh compliance review against the current state law before signing contracts.

## State-by-state catalog

### California — K-12 POPIPA (renamed SOPIPA) + AB 1584 + 2025-2026 AI legislation

**K-12 POPIPA — K-12 Pupil Online Personal Information Protection Act (renamed from SOPIPA via AB 801, 2025)**
- **Citation:** Cal. Bus. & Prof. Code §§ 22584-22585
- **Original effective date:** January 1, 2016 (as SOPIPA); renamed 2025
- **Key provisions:**
  - No targeted advertising based on student data
  - No selling or renting of student information
  - No creating profiles for non-educational purposes
  - Maintain reasonable security procedures
  - Delete student data on district request
- **2025 rename note:** `[VERIFY-WITH-COUNSEL]` whether AB 801 also tightened substantive provisions beyond the name change
- **Source:** https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml?lawCode=BPC&sectionNum=22584

**AB 1584 — Student data contract requirements**
- **Citation:** Cal. Ed. Code § 49073.1
- **Key provisions:** Mandatory contract terms for districts using third-party online services for student data

**AB 2013 — Generative AI training-data transparency (effective January 1, 2026)**
- **Key provisions:** Developers of generative AI systems must publicly disclose training data
- **Relevance:** applies to AI features if classified as GenAI. Document the training-data disclosure posture before any AI feature ships to a CA LEA
- **Source:** https://leginfo.legislature.ca.gov/

**AB 1159 — Pending: ban on training AI models on student data**
- **Status:** `[VERIFY-WITH-COUNSEL]` enactment status; was in committee as of January 2026 hearings
- **Relevance:** if enacted, would prohibit AI subprocessors from training on student data sent through the application, even when DPA-permitted today. Already aligned with the existing posture per `docs/security/SECURITY_ARCHITECTURE.md` (training prohibited contractually)

### Colorado — Privacy Act + Student Data Transparency and Security Act

- **Citation:** Colo. Rev. Stat. § 22-16-101 et seq.
- **Key provisions:**
  - Annual public report of all student data agreements
  - District-level data inventory requirements
  - Vendor obligations on data handling, security, breach notification
- **Source:** https://www.cde.state.co.us/dataprivacyandsecurity

### Connecticut — Student Data Privacy Act + 2025-2026 amendments

**Student Data Privacy Act**
- **Citation:** Conn. Gen. Stat. § 10-234aa et seq.
- **Key provisions:**
  - Mandatory contract provisions for "consultants" of public schools
  - Public disclosure of contracts on district websites
  - Data deletion on contract termination
- **Source:** https://portal.ct.gov/SDE/Student-Data-Privacy

**SB 1295 — Connecticut Data Privacy Act amendments (signed June 2025, effective July 1, 2026)**
- **Key provisions:**
  - Adds "neural data" to the sensitive data definition
  - Absolute restriction on processing minor (under-18) personal data for targeted advertising or sale
- **Relevance:** any biometric or neural-sensor work needs CT-specific scrutiny. Under-18 targeted-ad / sale ban is absolute, not opt-out
- **Source:** https://www.bytebacklaw.com/2025/06/connecticut-enacts-significant-amendments-to-states-data-privacy-law/

**Public Act 26-1 (effective March 3, 2026)**
- **Key provisions:** Eliminates the prior district requirement to report software used as part of IEP or 504 plans
- **Relevance:** lowers a reporting burden for CT districts; reduces vendor-disclosure documentation expected per CT districts

### Illinois — SOPPA (Student Online Personal Protection Act)

- **Citation:** 105 ILCS 85
- **Effective:** 2014; major amendments 2021
- **Key provisions:**
  - Operator restrictions on use, retention, disclosure of covered information
  - Mandatory deletion on parent request (within 30 days)
  - Breach notification (within 30 days)
  - Contracted operators must comply with school policies
  - Required school district disclosures of operator contracts (annually)
- **Source:** https://www.ilga.gov/legislation/ilcs/ilcs5.asp?ActID=4032&ChapterID=17
- **ISBE guidance:** https://www.isbe.net/Pages/SOPPA.aspx

### Maryland — Maryland Student Data Privacy Act

- **Citation:** Md. Code Ann., Educ. § 4-131
- **Key provisions:**
  - Vendor obligations
  - Stricter than COPPA in EdTech-specific provisions

### Massachusetts — Student Records Regulations

- **Citation:** 603 CMR 23.00
- **Status:** Pre-FERPA state regulation still in force; more protective than FERPA in some areas
- **Key provisions:**
  - Specific access, amendment, and consent rules
  - Stricter limits on disclosure than FERPA

### New York — Education Law § 2-d

- **Citation:** N.Y. Educ. Law § 2-d
- **Effective:** 2014; major Parents' Bill of Rights provisions
- **Key provisions:**
  - Parents' Bill of Rights for Data Privacy and Security (mandatory document)
  - Comprehensive data security plan required
  - Annual disclosure of all vendors handling student data
  - Specific breach notification requirements
- **Source:** https://www.nysed.gov/data-privacy-security

### Texas — TEC Chapter 32 + 19 TAC Chapter 25 + SCOPE Act (HB 18)

**TEC Chapter 32 + 19 TAC Chapter 25**
- **Citation:** Tex. Educ. Code Chapter 32, Subchapter B; 19 Tex. Admin. Code Chapter 25
- **Key provisions:**
  - Confidentiality of certain personally identifiable information
  - Specific provisions on biometric data
  - SBEC (State Board for Educator Certification) rules

**SCOPE Act (HB 18, signed June 2023; effective stages 2023-2024)**
- **Citation:** Tex. Bus. & Com. Code §§ 509.001 et seq.; Tex. Fam. Code provisions
- **Status:** Article 3 in effect for SY 2023-24; remainder Sept. 1, 2024. Monitoring/filtering provisions **partially enjoined August 2024** by federal court.
- **Key provisions for digital service providers serving Texas minors:**
  - Parental controls and account registration requirements
  - Content restrictions for known-minor users
  - Data collection and disclosure restrictions for minors
- **Relevance:** operates alongside TEC Chapter 32 in Texas LEAs; verify whether SCOPE Act applies to school-authorized EdTech use (the school-official theory may carve out; verify with counsel)
- **Source:** https://capitol.texas.gov/tlodocs/88R/billtext/pdf/HB00018H.pdf

### Utah — Student Data Protection Act

- **Citation:** Utah Code § 53E-9-301 et seq.
- **Key provisions:**
  - Notice and consent
  - Vendor agreement requirements
  - Student Data Officer at each LEA

### Virginia — Student data privacy

- **Citation:** Va. Code § 22.1-289
- **Key provisions:**
  - Annual data inventory by school divisions
  - Specific vendor contract requirements

### Washington — RCW 28A.604

- **Citation:** Wash. Rev. Code § 28A.604
- **Key provisions:**
  - Vendor obligations on student data
  - Public reporting

## AI-in-education legislation (2025-2026 wave)

Active legislative trend. Spring 2026 sessions are tracking 134 bills across 31 states per FutureEd. Common patterns: disclosure requirements, human-in-the-loop mandates, parent opt-in for student profiling, bans on AI for high-stakes decisions, restrictions on training AI on student data.

**Enacted 2025 laws touching AI-in-education:** Illinois, Louisiana, Nevada, New Mexico (per CDT roundup). `[VERIFY-WITH-COUNSEL]` specific scope per state before signing LEA contracts.

**Notable 2026 session bills (not all enacted as of May 2026):**

- **California AB 1159** — would ban using student data to train AI models. Pending.
- **California AB 2013** — generative-AI training-data transparency (enacted, effective Jan 1, 2026). Applies to GenAI developers.
- **Idaho SB 1227** — data privacy protections for AI tools used in schools. Pending.
- **Oklahoma SB 1734** — AI in schools only under educator supervision; annual parent disclosure; ban on AI for high-stakes decisions. Pending.
- **South Carolina HB 5253** — strict; parental opt-in, annual disclosure of AI tools and data practices, ban on AI replacing licensed teachers, automated-decision restrictions. Pending.

**Pattern for the application's posture:** AI-as-augmentation (per architectural principle #12 in `CLAUDE.md`) aligns with the human-in-the-loop and no-high-stakes-decision requirements. The training-on-data restriction aligns with `docs/security/SECURITY_ARCHITECTURE.md` threat #5 mitigations. Disclosure requirements need per-LEA implementation in customer-facing materials.

**Trackers:**
- FutureEd 2026 State AI in Education Legislative Tracker: https://www.future-ed.org/legislative-tracker-2026-state-ai-in-education-bills/
- CDT 2025 State AI in Education Roundup: https://cdt.org/insights/states-focused-on-responsible-use-of-ai-in-education-during-the-2025-legislative-session/

## Cross-cutting themes

When evaluating any new state, look for these dimensions:

### 1. Contract requirements
Most states require specific provisions in district-vendor contracts (data security, use limitation, deletion timeline, breach notification, subprocessor restrictions). Maintain a per-state contract addendum library.

### 2. Public disclosure / transparency
Many states require districts to publicly list all vendors handling student data. Be ready to be on those lists with consistent, clear descriptions.

### 3. Breach notification windows
Vary from "without unreasonable delay" to specific timelines (30 days in Illinois, 60 days in some). Set internal notification windows to the strictest applicable, not the average.

### 4. Data deletion timelines
On contract termination, parent request, or student exit. Some states require deletion within 30 days; others are vaguer. Implement the strictest window.

### 5. Targeted advertising bans
Nearly universal. Don't engage in this regardless of state.

### 6. Selling of data
Universally prohibited for student data. The definition of "sale" varies; aligned-but-not-identical to consumer privacy law definitions.

### 7. AI / automated decision-making
Increasingly addressed. Some states are passing legislation specifically on AI use in education. Active legislative area in 2025-2026.

### 8. Biometric data
Several states have additional restrictions on biometric identifiers. If the application ever captures voice, facial, or fingerprint data, treat this as elevated risk.

## How to manage state compliance operationally

### Per-state compliance dossier

Maintain a per-state file at `docs/compliance/states/<state-code>.md` covering:

1. Active citation(s)
2. Effective date and most recent amendment
3. Key vendor obligations
4. Contract addendum template
5. Breach notification timeline
6. Data deletion timeline
7. Public disclosure requirements (if any)
8. Known gotchas
9. Last legal review date

### State expansion review checklist

Before signing a district in a new state:

- [ ] State privacy law identified and read
- [ ] Contract template reviewed against state requirements
- [ ] Breach notification timeline understood
- [ ] Deletion timeline understood
- [ ] State-specific Ed-Fi extensions identified (if relevant)
- [ ] State IEP system identified (e.g., SEIS in CA, CT-SEDS in CT)
- [ ] State approved-vendor process initiated if required
- [ ] Subprocessor inventory updated if needed

### Quarterly legislative tracker

Many states pass new student privacy laws or amend existing ones in spring legislative sessions. Quarterly review (Jan, Apr, Jul, Oct) of legislative trackers.

Useful trackers (community-maintained):
- A4L Student Data Privacy Consortium: https://privacy.a4l.org/
- FIRE student data privacy: https://www.thefire.org/research-learn/student-data-privacy
- IAPP student privacy tracker: https://iapp.org/

## State-run IEP systems

A few states operate state-run IEP systems that districts use uniformly. Integration with these is its own track:

| State | System | Vendor / operator | Notes |
|---|---|---|---|
| California | SEIS | California Department of Education | Used by ~95% of CA districts. State-approved vendor agreement required. SFTP. |
| Connecticut | CT-SEDS | CT State Department of Education | All CT public districts. SFTP. |
| New York | SESIS | NYC Department of Education | NYC-specific; SESIS is the NYC platform |
| Maryland | OneStop | Maryland State Department of Education | Statewide |

State-run systems are uniquely heterogeneous. Budget heavily for integrations with them — they're slower, more politically complex, and require partnership work before code (as flagged in the connector framework architecture).

## State IDEA evaluation timelines (for compliance service)

For the `compliance/state/<state>.py` policy templates referenced in `.claude/rules/compliance.md`. Stub list of evaluation-timeline policies per state the application currently serves. Fill in citations as the per-state file is created.

| State | Evaluation days | Day kind | Notes | Source |
|---|---|---|---|---|
| Federal default | 60 | calendar | IDEA 34 CFR § 300.301(c)(1) | https://www.ecfr.gov/current/title-34/part-300/section-300.301 |
| California | 60 | calendar | Exclusive of school breaks of 5+ days per Ed. Code § 56043 | https://leginfo.legislature.ca.gov/ |
| Texas | 45 | school | Per 19 TAC § 89.1011; stricter than federal | https://tea.texas.gov/ |
| Florida | 60 | calendar | Per Fla. Stat. § 1003.5715 / Rule 6A-6.0331; excludes school holidays and summer break | http://flrules.elaws.us/fac/6a-6.0331 |
| Washington | 35 | school | Per WAC 392-172A-03005; stricter than federal | https://app.leg.wa.gov/wac/ |
| New York | 60 | calendar | Per 8 NYCRR § 200.4(b) | https://www.nysed.gov/ |
| Illinois | 60 | school | Per 23 IAC 226.110 | https://www.isbe.net/ |
| Idaho | 60 | calendar | Federal default; the prior school-break exclusion was phased out for FY 2025-2026 (clock runs through breaks per Idaho SDE) | https://www.sde.idaho.gov/wp-content/uploads/2025/06/Idaho-Special-Education-Manual-Redline-Dec-2025.pdf |
| Wisconsin | 60 | calendar | Federal default per Wis. Stat. § 115.78(3); § 115.777 covers referral/data review, not the 60-day rule | https://docs.legis.wisconsin.gov/statutes/statutes/115/v/78/3/c |
| Colorado | 60 | calendar | Federal default per 1 CCR 301-8 | https://www.cde.state.co.us/cdesped |
| Maryland | 60 | calendar | Per COMAR 13A.05.01.06 | http://www.dsd.state.md.us/ |
| Virginia | 65 | business | Per 8VAC20-81-70 (§ 50 is Child Find, not eval); "business days" excludes weekends/holidays | https://law.lis.virginia.gov/admincode/title8/agency20/chapter81/section70/ |
| Utah | 45 | school | Per USBE Special Education Rules; `[VERIFY-WITH-COUNSEL]` exact paragraph against the 2024 USBE Rules PDF (Roman-numeral III.E.2 is older nomenclature) | https://www.schools.utah.gov/specialeducation |
| Connecticut | 45 | school | From referral to IEP implementation, exclusive of consent-wait time (CT SDE guidance) | https://portal.ct.gov/-/media/SDE/Performance/Data-Collection/Help-Sites/Evaluation-Timelines/FS_EvaluationTimelines.pdf |
| Massachusetts | 45 | school working days | Per 603 CMR 28.05 (full team process); 30-day figure is the assessment sub-step under 28.04, not the headline timeline | https://www.doe.mass.edu/lawsregs/603cmr28.html?section=05 |

Where the application enters a new state, **add a row before signing the LEA.** Citations need verification at the time of code-writing; `[VERIFY]` markers stay until the per-state policy file in `compliance/state/<state>.py` cites the statute with a verification date per `.claude/rules/compliance.md`.

**Triennial reevaluation, annual review, and transition planning deadlines** also vary by state. Add columns to this table or per-state files as they're built. See `.claude/rules/edfi-sedm.md` and the SEDM spec at https://datastandardsunited.org/ceds-sedm for the broader IDEA timeline shape.

## References

- A4L Student Data Privacy Consortium (community DPA templates, state tracker): https://privacy.a4l.org/
- **Common Sense Privacy** (vendor privacy evaluations, district-side compliance reference): https://privacy.commonsense.org/
- **Student Privacy Compass** (active community tracker that has filled the role FERPA|Sherpa held; FERPA|Sherpa domain was lost in 2025): https://studentprivacycompass.org/
- National Conference of State Legislatures education page: https://www.ncsl.org/education
- IAPP student privacy: https://iapp.org/
- **NCES District/School ID lookup** (canonical IDs for `nces_lea_id` and `nces_school_id` columns per `architecture/data-model.md`): https://nces.ed.gov/ccd/districtsearch/ and https://nces.ed.gov/ccd/schoolsearch/
- **OSEP IDEA data center** (Indicator 11/13 definitions and state submissions): https://sites.ed.gov/idea/data/
- **U.S. Department of Education Privacy Technical Assistance Center (PTAC)** (FERPA guidance, model DPA): https://studentprivacy.ed.gov/

## Cross-references in this repo

- `.claude/rules/compliance.md` — federal compliance discipline (FERPA, IDEA, COPPA inline)
- `.claude/rules/security.md` — FERPA disclosure log + audit log discipline
- `docs/compliance/GROUNDING_SOURCES.md` — authoritative federal URLs
- `docs/compliance/GROUNDING_SOURCES.md` — overall framework registry
- `docs/partners/ednition.md` — covers state-run system support patterns
