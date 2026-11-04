# ASCE GOVBEN CaaS — Red Hat Enterprise Linux & OpenShift Support Matrix

**Document Identifier:** `ASCE-DOC-RHEL-OCP-MATRIX-2026-V10`  
**Product Name:** ASCE GOVBEN CaaS (Compliance-as-a-Service)  
**Product Version:** `10.0.0` (Container Release `1`)  
**Vendor / Publisher:** ASCE Technologies LLC (Louisville, KY & Atlanta, GA)  
**Website:** [https://ascetech.org](https://ascetech.org) | **Support Email:** `support@ascetech.org` / `t.bullitt@ascetech.org`  
**Red Hat Partner Org ID:** `20792660` | **Partner MDM:** `MDM-P002000000044183`  
**Red Hat Connect Product ID:** `6ae3354d17b7f519e40a0c89`  
**Red Hat Ecosystem Catalog ID:** `6ae487b483fefa429257d504`  
**Container Base Image:** `registry.access.redhat.com/ubi9/python-311:latest` (UBI 9)  
**Preflight Certification Status:** Passed 10/10 Checks (Security Health Index Grade A, 0 CVEs)  
**Effective Date:** 2026-11-04  

---

## 1. Executive Summary & Support Statement
ASCE Technologies LLC certifies and fully supports **ASCE GOVBEN CaaS v10.0.0** deployed as a containerized compliance decision-support gateway running on **Red Hat Enterprise Linux (RHEL)** and **Red Hat OpenShift Container Platform (OCP)** across bare metal, virtualized (KVM/RHV), private cloud, and certified public cloud infrastructures.

All container images are built strictly on Red Hat Universal Base Image 9 (UBI 9), execute as non-root user (`UID 1001`), preserve Red Hat end-user licensing agreements under `/licenses/LICENSE`, and operate seamlessly under strict **SELinux Enforcing mode** without requiring security policy modifications.

---

## 2. Red Hat Enterprise Linux (RHEL) Platform Support Matrix

ASCE GOVBEN CaaS is validated, tested, and actively supported across the following RHEL releases on **x86_64** (AMD64) and **aarch64** (ARM64) architectures:

| RHEL Platform Version | Compatibility Tier | Support Lifecycle Status | Architectures Supported | Runtime Environments | SELinux Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **RHEL 10.1** | Forward Compatible | Next-Gen Validated | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 10.0** | Compatible | Next-Gen Validated | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 9.8** | Fully Supported | Active / Certified | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 9.6** | Fully Supported | Extended Update Support (EUS) | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 9.5** | Fully Supported | Active Maintenance | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 9.4** | Fully Supported | Extended Update Support (EUS) | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 9.3** | Fully Supported | Maintenance | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 9.2** | Fully Supported | Maintenance (EUS) | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 9.1** | Fully Supported | Maintenance | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 9.0** | Fully Supported | Maintenance (EUS) | `x86_64`, `aarch64` | Bare-metal, Virtualized, Cloud | Enforcing (Zero disabling) |
| **RHEL 8.10** | Supported | Maintenance Support 2 | `x86_64`, `aarch64` | Podman / CRI-O on RHEL 8 | Enforcing (Zero disabling) |
| **RHEL 8.8** | Supported | Extended Update Support (EUS) | `x86_64`, `aarch64` | Podman / CRI-O on RHEL 8 | Enforcing (Zero disabling) |
| **RHEL 8.6** | Supported | Extended Update Support (EUS) | `x86_64`, `aarch64` | Podman / CRI-O on RHEL 8 | Enforcing (Zero disabling) |

### RHEL Image Mode Support
ASCE GOVBEN CaaS supports **Image Mode for Red Hat Enterprise Linux** using `bootc` container-based OS management, allowing the ASCE pre-commit gateway to be deployed as an immutable operating system appliance on edge workstations and field triage centers.

---

## 3. Red Hat OpenShift Container Platform (OCP) Support Matrix

ASCE GOVBEN CaaS is validated and supported across all active OpenShift versions and enterprise managed services:

| OpenShift Version | Compatibility Tier | Minimum OCP Release | Supported Architectures | Deployment Flavors | Storage & Ingress |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **OpenShift 4.20** | Compatible | `4.20.0` | `x86_64`, `aarch64` | OCP, ROSA, ARO, ROKS | ODF, Ceph, Cloudant, IBM COS |
| **OpenShift 4.19** | Compatible | `4.19.0` | `x86_64`, `aarch64` | OCP, ROSA, ARO, ROKS | ODF, Ceph, Cloudant, IBM COS |
| **OpenShift 4.18** | Fully Supported (Certified) | `4.18.0` | `x86_64`, `aarch64` | OCP, ROSA, ARO, ROKS | ODF, Ceph, Cloudant, IBM COS |
| **OpenShift 4.17** | Fully Supported (Certified) | `4.17.0` | `x86_64`, `aarch64` | OCP, ROSA, ARO, ROKS | ODF, Ceph, Cloudant, IBM COS |
| **OpenShift 4.16** | Fully Supported (EUS) | `4.16.0` | `x86_64`, `aarch64` | OCP, ROSA, ARO, ROKS | ODF, Ceph, Cloudant, IBM COS |
| **OpenShift 4.14** | Supported (EUS) | `4.14.0` | `x86_64`, `aarch64` | OCP, ROSA, ARO, ROKS | ODF, Ceph, Cloudant, IBM COS |
| **OpenShift 4.12** | Supported (Maintenance) | `4.12.0` | `x86_64`, `aarch64` | OCP, ROSA, ARO, ROKS | ODF, Ceph, Cloudant, IBM COS |

### Supported OpenShift Deployment Flavors
1. **Red Hat OpenShift on IBM Cloud (ROKS):** Primary IBM Cloud Code Engine and multi-tenant OpenShift environment.
2. **Red Hat OpenShift Service on AWS (ROSA):** GovCloud and commercial AWS deployments.
3. **Azure Red Hat OpenShift (ARO):** Federal DoD IL5/IL6 and Azure Government deployments.
4. **Self-Managed OpenShift Container Platform (OCP):** On-premise bare metal and VMware vSphere / Red Hat OpenShift Virtualization.

---

## 4. Security, Compliance & Governance Specifications

- **Container Security:** Non-root execution (`USER 1001`), rootless Podman compliant, OpenShift restricted-v2 SCC compliant.
- **SELinux:** 100% compliant with SELinux in **Enforcing mode** using standard `container_t` policy. Does not require privileged containers, host network access, or `CAP_SYS_ADMIN`.
- **Preflight 10/10 Verification:** Passed all OpenShift Preflight certification tests (HasLicense, HasUniqueTag, HasNoProhibitedPackages, HasRequiredLabel, RunAsNonRoot, UnderLayerSize, HasExecutable, etc.).
- **Vulnerability Posture:** Red Hat Security Health Index **Grade A** with zero unpatched critical or high vulnerabilities.
- **Federal Governance Compliance:** Built to enforce OMB Memorandum M-24-10, NIST SP 800-53 Rev. 5, NIST SP 800-63B AAL3 (PIV/CAC authentication), and Title 5 U.S.C. human-in-the-loop decision requirements.

---

## 5. Support Level Agreement (SLA) & Contact Information

ASCE Technologies LLC provides Tier-1 through Tier-3 enterprise software support for ASCE GOVBEN CaaS running on certified Red Hat platforms:

- **Severity 1 (Critical Outage):** Initial response within 1 hour; 24x7 resolution effort.
- **Severity 2 (High / Degraded):** Initial response within 4 hours.
- **Severity 3 (General Inquiries / Upgrades):** Initial response within 1 business day.

**Technical Partner Support Contact:**
- **Primary Support Desk:** `support@ascetech.org`
- **Technical Lead:** Todd Bullitt (`t.bullitt@ascetech.org`)
- **Support Portal:** [https://ascetech.org/support](https://ascetech.org/support)
- **Live Decision Support Gateway:** [https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/](https://asce-gateway.2fkm9wojoieo.us-south.codeengine.appdomain.cloud/)
- **Documentation Repository:** [https://github.com/tbullitt18-collab/asce-technologies](https://github.com/tbullitt18-collab/asce-technologies)
