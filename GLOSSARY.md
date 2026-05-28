# Glossary

Standards-anchored vocabulary for the rostering framework. Terms used in code, docs, and conversation. **When standards define a term, the framework uses the standard's definition.** Internal coinages are marked explicitly.

## A

**A4L (Access 4 Learning Community)** — Standards community body coordinating SEDM with CEDS. Successor to SIF Association. https://www.a4l.org/

**Access token** — Short-lived credential for outbound API calls. Per `.claude/rules/integration-protocol.md`, cached per `(partner, lea_id)`.

**ADR (Architecture Decision Record)** — Document capturing a significant architectural decision, its context, and the reasoning. Template at `docs/decisions/adr-template.md`.

**AGS (Assignment and Grade Services)** — LTI Advantage service for grade passback from a Tool to an LMS. Per `.claude/rules/lti.md`.

**Aggregator** — A partner that provides a unified API across many district SIS systems. Ednition, EdLink, Clever, ClassLink are aggregators. Per `docs/partners/comparison.md`.

**Annual review** — Per IDEA 34 CFR § 300.324(b)(1), the IEP team's annual review of the IEP. Required within 365 days of the prior review. Per `.claude/rules/compliance.md`.

**Audit log** — Append-only record of operations on student data. Distinct from application logs and from the FERPA disclosure log. Per `.claude/rules/security.md`.

## B

**Bounded context** — A logical boundary within the codebase (DDD term). Rostering, IEP management, LTI launch, and compliance are example bounded contexts.

## C

**Canonical** — The framework's internal representation of an entity, decoupled from any specific partner's wire format. Connectors translate to and from canonical.

**CASE (Competencies and Academic Standards Exchange)** — 1EdTech standard for competency and standards data. Future scope.

**Caliper Analytics** — 1EdTech learning event analytics standard. Future scope.

**CEDS (Common Education Data Standards)** — Federal-level neutral education data vocabulary. SEDM is proposed for CEDS inclusion. https://ceds.ed.gov/

**ClassLink** — Identity and rostering aggregator. SSO + rostering combined. https://www.classlink.com/

**Clever** — Identity and rostering aggregator. Most-installed K-12 SSO platform. https://clever.com/

**Connector** — A pluggable integration with an external partner. Implements the `Connector` protocol per `.claude/rules/integration-protocol.md`.

**COPPA (Children's Online Privacy Protection Act)** — Federal law restricting collection of personal info from children under 13. Per `.claude/rules/compliance.md`.

## D

**Data Sharing Agreement (DSA)** — Contract between the vendor and a district authorizing student data access under FERPA's school-official exception. Per `.claude/rules/compliance.md`.

**Deep Linking (LTI)** — LTI Advantage flow where a teacher selects specific Tool content from inside the LMS authoring UI. Per `.claude/rules/lti.md`.

**Descriptor (Ed-Fi)** — URI-typed enumerated value in Ed-Fi. E.g., `uri://ed-fi.org/DisabilityDescriptor#SpecificLearningDisability`. Per `.claude/rules/edfi-sedm.md`.

**District** — Colloquial term for an LEA. Customer-facing copy uses "district"; code and schema use `lea_id`. See `LEA`.

**Disclosure log** — FERPA 34 CFR § 99.32 mandated record of disclosures of personally identifiable information from education records that fall outside the school-official exception. Distinct from audit log. Per `.claude/rules/compliance.md`.

## E

**Education record (FERPA)** — Records directly related to a student and maintained by an educational agency. Broader scope than people assume; includes application-produced AI summaries. Per `.claude/rules/compliance.md`.

**Ed-Fi Alliance** — Non-profit consortium owning the Ed-Fi data standard and ODS/API. https://www.ed-fi.org/

**Ed-Fi Data Standard** — Comprehensive K-12 data standard. Current version 6.1 (May 2026). Includes SEDM. Per `.claude/rules/edfi-sedm.md`.

**Ed-Fi ODS** — Operational Data Store. The database backing an Ed-Fi deployment. Implemented in Postgres or SQL Server.

**Ed-Fi ODS/API** — REST API exposing the Ed-Fi ODS. Current version 7.3.2. Per `.claude/rules/edfi-sedm.md`.

**Ed-Fi OneRoster Service** — Bridge service exposing OneRoster 1.2 endpoints from an Ed-Fi ODS. New in 2026. Per `.claude/rules/edfi-sedm.md`.

**Ednition** — RosterStream rostering aggregator (private-label, direct-to-SIS). Per `docs/partners/ednition.md`.

**EdLink** — Rostering aggregator. Graph API + User Data API + OneRoster export. Per `docs/partners/edlink.md`.

**Effective date** — In the temporal model, the first day an IEP version is valid. Inclusive. Per `.claude/rules/temporal-model.md`.

**End date** — In the temporal model, the last day an IEP version is valid. Inclusive. `NULL` means currently open. Per `.claude/rules/temporal-model.md`.

**Enrollment** — The relationship between a student and a class. OneRoster: `Enrollment`; Ed-Fi: `StudentSectionAssociation`.

**ESSA (Every Student Succeeds Act)** — Federal education law. ESSA Tier 4 evidence is the entry tier for federally-funded EdTech procurement claims.

**Event bus** — Internal pub/sub system for cross-bounded-context communication. Per `.claude/rules/events.md`.

## F

**FAPE (Free Appropriate Public Education)** — IDEA's core obligation. Per `.claude/rules/compliance.md`.

**FERPA (Family Educational Rights and Privacy Act)** — Federal student records privacy law. Per `.claude/rules/compliance.md`.

## G

**Gradebook (OneRoster service)** — OneRoster's service for class-context grades. Separate from LTI AGS.

## I

**1EdTech** — Standards body owning OneRoster, LTI, Caliper, CASE, QTI. Formerly IMS Global Learning Consortium. https://www.1edtech.org/

**IDEA (Individuals with Disabilities Education Act)** — Federal special education law. Per `.claude/rules/compliance.md`.

**Idempotency key** — Unique identifier on a write request used to deduplicate on the receiving side. Per `.claude/rules/integration-protocol.md`.

**IEP (Individualized Education Program)** — Legally binding document under IDEA describing a student's special education services. Per `.claude/rules/compliance.md`.

**IEP snapshot** — One version of an IEP. Append-only; new versions supersede old ones. Per `.claude/rules/temporal-model.md`.

**IEP team** — The legal body (parents, teachers, specialists, LEA representative) that authors and amends a student's IEP. AI assists; the team decides.

**Indicator 11, 13 (IDEA Part B)** — OSEP-tracked metrics for IDEA Part B compliance. Indicator 11: initial evaluation timeline compliance. Indicator 13: transition planning compliance. Per `.claude/rules/compliance.md`.

**Initial evaluation** — First evaluation to determine if a student qualifies for special education under IDEA. 60-day federal default deadline per IDEA 34 CFR § 300.301(c)(1); see `.claude/rules/compliance.md`.

## J

**JWT (JSON Web Token)** — Signed token format used for authentication. Per `.claude/rules/security.md`. Used for inbound user auth (RS256), LTI launch (RS256), partner auth (sometimes private_key_jwt).

## L

**LEA (Local Education Agency)** — The framework's tenant unit. Federal term used in IDEA, ESSA, Ed-Fi, OneRoster, SEDM, CEDS. Covers traditional school districts, standalone charter schools, and CMO-operated charters. Code and schema identifier is `lea_id`. Customer-facing copy says "district." Per `.claude/rules/multi-tenancy.md`.

**Line item (LTI AGS)** — LMS gradebook column. AGS pushes scores to line items. Per `.claude/rules/lti.md`.

**LRE (Least Restrictive Environment)** — IDEA principle that students with disabilities are educated alongside non-disabled peers to the maximum extent appropriate. Per `.claude/rules/compliance.md`.

**LTI 1.3 with Advantage** — 1EdTech standard for launching tools inside LMSes. Current version. Per `.claude/rules/lti.md`.

## M

**Manifestation determination** — Per IDEA 34 CFR § 300.530(e), required review when a student with an IEP is removed for more than 10 cumulative school days. Per `.claude/rules/compliance.md`.

**Multi-tenancy** — Per `.claude/rules/multi-tenancy.md`, every entity scoped by `lea_id`. Single application instance, many isolated districts.

## N

**NRPS (Names and Roles Provisioning Service)** — LTI Advantage service for fetching context membership (class roster). Per `.claude/rules/lti.md`.

## O

**OneRoster** — 1EdTech rostering standard. Current version 1.2. Per `.claude/rules/oneroster.md`.

**OSEP (Office of Special Education Programs)** — U.S. Department of Education office enforcing IDEA. https://sites.ed.gov/idea/

**Outbox pattern** — Transactional event publishing: write event to `event_outbox` table in same transaction as domain change; publisher worker reads outbox. Per `.claude/rules/events.md`.

## P

**Partner** — External system or service the framework integrates with. Includes aggregators (Ednition, EdLink), direct sources (Clever, ClassLink, Ed-Fi state systems), and IEP system vendors (Frontline, PowerSchool Special Programs, SEIS, CT-SEDS).

**PLAAFP (Present Levels of Academic Achievement and Functional Performance)** — Required IEP element. Per `.claude/rules/compliance.md`.

**Procedural safeguards** — IDEA-mandated parental rights (prior written notice, consent, due process, etc.). Per `.claude/rules/compliance.md`.

## R

**Reconciliation** — Periodic comparison of local data against partner data to detect drift. Pattern is per-partner (push-event, pull-snapshot, file-transfer, or tiered escalation). See `docs/concepts/sync-patterns.md`.

**RosterStream** — Ednition's rostering API. Per `docs/partners/ednition.md`.

## S

**School official exception (FERPA)** — 34 CFR § 99.31(a)(1)(i)(B). Allows disclosure of education records to school officials with legitimate educational interest without parental consent. The typical basis for EdTech vendor operation. Per `.claude/rules/compliance.md`.

**Section (Ed-Fi)** — Ed-Fi's class concept. Natural key combines `(localCourseCode, schoolId, schoolYear, sectionIdentifier, sessionName)`.

**SEDM (Special Education Data Model)** — Ed-Fi extension in early access (6.1) modeling IEP and IDEA event data. Per `.claude/rules/edfi-sedm.md`.

**SEIS** — California's state-run IEP system. Used by ~95% of CA districts. https://www.seis.org/

**SIS (Student Information System)** — District-level system of record for students, classes, enrollments. PowerSchool, Infinite Campus, Skyward, Veracross, etc.

**SOPPA (Student Online Personal Protection Act)** — Illinois student privacy law (105 ILCS 85). Per `docs/compliance/state-privacy-laws.md`.

**SOPIPA** — California's analog to SOPPA. Cal. Bus. & Prof. Code §§ 22584-22585. Per `docs/compliance/state-privacy-laws.md`.

**Source ID (`sourcedId`)** — OneRoster's unique identifier per resource. Opaque string. Per `.claude/rules/oneroster.md`.

**SPPO (Student Privacy Policy Office)** — DOE office enforcing FERPA. https://studentprivacy.ed.gov/

**Supersedes** — In the temporal model, a snapshot's `supersedes_id` points to the prior version it replaces. Per `.claude/rules/temporal-model.md`.

## T

**Temporal snapshot** — An append-only versioned record. Used for IEP versions. Per `.claude/rules/temporal-model.md`.

**Tool (LTI term)** — An application launched inside an LMS Platform. Per `.claude/rules/lti.md`.

**Transition plan** — IDEA-required plan for students aged 16+ (earlier in some states) describing transition from school to post-school activities. Per `.claude/rules/compliance.md`.

**Triennial reevaluation** — Per IDEA 34 CFR § 300.303(b)(2), required reevaluation every 3 years of a student's IDEA eligibility. Per `.claude/rules/compliance.md`.

## U

**UDM (Unifying Data Model)** — Ed-Fi's underlying data model. SEDM is an extension of UDM.

**Upsert** — Insert-or-update operation. Common pattern in connector data ingest.

## V

**Validity window** — In the temporal model, the `[effective_date, end_date]` range during which a snapshot is in effect. Per `.claude/rules/temporal-model.md`.

## Cross-references

- Per-framework references: `docs/standards/`, `docs/compliance/`
- Per-partner references: `docs/partners/`
- Per-rule references: `.claude/rules/`
