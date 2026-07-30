# Bhoonidhi API access request — draft

**Status:** draft, not sent. Review, fill the bracketed fields, send from
roynayanroy14@gmail.com (or the institutional address if you have one — a college
domain reads better for a research request).

**To:** bhoonidhi@nrsc.gov.in
**Subject:** Query regarding programmatic / API access to Bhoonidhi open data — student research project

---

Respected Sir/Madam,

I am Nayan Roy, a BTech Information Technology student at Guru Nanak Institute of
Technology, Kolkata (graduating 2028). I am a registered Bhoonidhi user
(username: nayanroy, registered under this email address).

I am developing an open-source, non-commercial research prototype called **Bhoomi** — a
server-side Earth Observation processing platform. It allows a user to define an area of
interest, search a satellite catalogue, and run raster analyses (NDVI, NDWI, NDBI and
two-date change detection) on the server, returning results as Cloud-Optimized GeoTIFFs
through an OGC API – Processes interface. The project is educational and will be released
under an open-source licence. It does not redistribute source imagery; it serves only
derived analytical products, with full attribution to the data provider.

The system currently works end to end using Sentinel-2 data. I would like to demonstrate it
with Indian Earth Observation data, and would be grateful for guidance on the following:

1. **Programmatic access.** Is there a documented API — for example a STAC-compliant or
   OGC-compliant catalogue search endpoint — for querying the Bhoonidhi archive
   programmatically? I could not locate documentation for one on the portal.

2. **Open-category data.** Following the Indian Space Policy 2023, data of 5 m resolution
   and coarser is free and open. For datasets such as Resourcesat-2/2A LISS-III and AWiFS,
   is scripted or bulk download of open data permitted for a registered user, and if so
   what is the correct mechanism?

3. **Access procedure.** If programmatic access requires registration of a fixed public IP
   address or a similar whitelisting arrangement, could you advise on the procedure and the
   information I would need to provide? I expect to host the service on a virtual private
   server with a static IPv4 address and can supply those details.

4. **Terms of use.** Are there specific attribution or terms-of-use requirements I should
   follow when publishing derived products (for example index rasters) generated from
   Bhoonidhi data? I want to ensure compliance before anything is made publicly available.

I am happy to provide further details of the project, its architecture, or its intended use,
and to comply with any conditions NRSC requires. I would also be glad to acknowledge NRSC/ISRO
appropriately in the published work.

Thank you for your time and for maintaining this resource.

With regards,

Nayan Roy
BTech Information Technology, Guru Nanak Institute of Technology, Kolkata
Email: roynayanroy14@gmail.com
Phone: [____________]
GitHub: https://github.com/NayanRoy14

---

## Notes before sending

- **Send it from roynayanroy14@gmail.com** — the address the Bhoonidhi account is registered
  under. Username `nayanroy` is stated in the opening line.
- **Add a phone number** — the one remaining blank. Indian government correspondence often
  comes back by phone rather than email.
- Question 2 is the one most likely to unblock you quickly. Even a "no API, but here is how
  to bulk download open data" answer is a usable outcome for the project.
- Question 3 states the static-IP situation without asserting it as fact — the whitelist
  requirement is what was found in July 2026, but the procedure may have changed.
- Keep this email; the **date you send it starts the 2027-01-15 clock** in PLAN.md §2.2.

## Sending it

1. Open Gmail, compose a new message.
2. **To:** `bhoonidhi@nrsc.gov.in`
3. **Subject:** `Query regarding programmatic / API access to Bhoonidhi open data — student research project`
4. Paste the body between the `---` markers above. Replace the phone placeholder.
5. Send, then fill in the date at the bottom of this file and commit it.

Expect a slow reply, or none. That is why §2.2 has a decision date rather than an open wait.

**Date sent:** ____________
**Reply received:** ____________
