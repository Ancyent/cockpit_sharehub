# Plan de implementare - plugin Cockpit "ShareHub" (nume de lucru)

Versiune: 1.1 - 17.11.2026

Repo: https://github.com/Ancyent/cockpit_sharehub - licenta **GPL-3.0-or-later**, public.

## 1. Scop

Un plugin Cockpit care inlocuieste modulul File Sharing de la 45Drives si administreaza dintr-un singur loc:

- share-uri cu **trei bife pe acelasi folder: SMB / NFS / FTP**;
- useri si grupuri locali, cu **parola sincronizata** intre sistem (PAM) si Samba, plus **home per user** pe SMB si FTP;
- **setarile globale Samba**, cu profile pentru clienti **macOS** si **Windows**;
- **Time Machine**, **Recycle bin**, **Previous Versions** (snapshot-uri ZFS/Btrfs);
- **Join in Active Directory** (winbind);
- livrare la clienti ca **pachet .deb** versionat, cu repo apt in faza finala.

## 2. Pornim de la ce exista deja (v0.2, testat)

| Componenta | Stare |
|---|---|
| Share-uri SMB in registry (`net conf`) | facut, testat |
| Publicare FTP per share: ProFTPD, chroot, bind mount, ACL dupa `valid users`, FTPS obligatoriu | facut, testat |
| Home per user: `[homes]` pe SMB, `/home/<user>` pe FTP cu izolare intre useri | facut, testat |
| Useri: creare, editare, parola sincronizata (`chpasswd` + `smbpasswd`), enable/disable cont SMB | backend facut si testat, UI nefacut |
| Validare + rollback: `proftpd -t`, `testparm` | facut |

## 3. Arhitectura

### 3.1 Sursa unica de adevar + reconciliere

Toata configuratia sta intr-un singur fisier, `/etc/sharehub/state.json`: share-uri, protocoale, setari globale si mod de autentificare. Backend-ul genereaza din el, idempotent:

| Tinta | Fisier / mecanism | Validare inainte de aplicare |
|---|---|---|
| Samba global + share-uri | registry (`net conf`), `include = registry` | `testparm -s` |
| NFS | `/etc/exports.d/sharehub.exports` + `/etc/nfs.conf.d/sharehub.conf` | `exportfs -ra`, cu rollback la eroare |
| FTP | `/etc/proftpd/conf.d/sharehub.conf` | `proftpd -t` |
| Folderele FTP | bind mounts in `/srv/ftp-shares`, linii fstab cu `x-sharehub` | `findmnt` |
| Cos de gunoi | systemd timer pentru retentie | - |
| Descoperire in retea | Avahi (macOS) + wsdd (Windows) | status serviciu |

Fiecare operatie are pasii: **render -> backup -> validare -> aplicare -> reload**. Daca validarea pica, se revine la starea anterioara. La fiecare scriere se pastreaza ultimele 10 snapshot-uri ale `state.json`, ca sa existe buton de "undo".

### 3.2 Backend

Pachet Python in `/usr/lib/sharehub/`, cu cate un modul pe zona: `samba`, `nfs`, `ftp`, `users`, `ad`, `snapshots`, `services`, `migrate`. Ramane un singur punct de intrare (`sharehub-helper <comanda>`), apelat cu `cockpit.spawn(superuser)`. Comunicarea e JSON pe stdin/stdout, ca acum. Fiecare apel ia un lock, ca doi admini sa nu scrie in paralel.

### 3.3 Migrare de pe 45Drives

Un wizard la prima rulare:

1. citeste share-urile din registry si `/etc/exports.d/cockpit-file-sharing.exports`;
2. le importa in `state.json`;
3. arata un diff;
4. recomanda dezinstalarea `cockpit-file-sharing`.

Plugin-ul nu sterge nimic al lor fara confirmare.

## 4. Functionalitati detaliate

### 4.1 Share editor

Editorul are urmatoarele sectiuni:

- **General:** nume, cale cu browser de foldere (custom, nu dialogul nativ), descriere, quota (daca filesystem-ul suporta: ZFS/XFS project quota).
- **Protocoale:** trei bife: SMB / NFS / FTP.
- **Acces SMB/FTP:** useri si grupuri (multiselect cu search), read-only, guest (doar SMB), vizibil in browsing.
- **NFS:** lista de clienti (host / subnet / `*`), fiecare cu rw/ro, root_squash / no_root_squash / all_squash (cu anonuid/anongid) si sync/async. UI-ul afiseaza clar avertismentul ca la NFS accesul se face pe UID si IP, nu pe parola.
- **Optiuni SMB:**
  - profil client: Auto / macOS / Windows;
  - Time Machine, cu cota;
  - Recycle bin, cu retentie in zile;
  - Previous Versions;
  - mod ACL: POSIX simplu sau ACL-uri Windows (`acl_xattr`).
- **FTP:** read-only doar pe FTP (ca acum).

### 4.2 Setari globale Samba

- **Identitate:** workgroup, server string, NetBIOS name, NetBIOS pornit/oprit.
- **Securitate:**
  - protocol minim (SMB2_10 implicit, SMB3 optional);
  - criptare (`smb encrypt`: off / desired / required);
  - signing;
  - SMB1 dezactivat permanent; nu apare ca optiune.
- **Profil macOS (global):**
  - `vfs objects = catia fruit streams_xattr`;
  - `fruit:metadata = stream`, `fruit:model`, `fruit:posix_rename`, `fruit:nfs_aces = no`, curatarea fisierelor AppleDouble goale;
  - publicare mDNS (Avahi) pentru Finder si Time Machine.
- **Profil Windows:**
  - wsdd, ca serverul sa apara in Network Neighborhood fara SMB1;
  - `map acl inherit`, `store dos attributes`, `acl_xattr` (optional);
  - ordinea corecta a modulelor VFS cand sunt combinate (fruit / acl_xattr / shadow_copy2 / recycle).
- **Performanta:** `server multi channel support`, aio, socket options. Implicit sunt sigure, cu explicatii in UI.

### 4.3 Time Machine

- `fruit:time machine = yes` si `fruit:time machine max size` per share.
- Recomandat: un sub-folder per user (`%U`), ca fiecare Mac sa aiba backup-ul lui.
- Verificare ca Avahi publica serviciul `_adisk._tcp`.
- **De validat in implementare:** comportamentul pe versiunea de Samba din fiecare distributie suportata.

### 4.4 Recycle bin

- `vfs_recycle` cu `repository = .recycle/%U`, `keeptree`, `versions`, `touch` si excluderi pentru fisiere temporare (`*.tmp`, `~$*`).
- Un timer systemd zilnic sterge ce e mai vechi decat retentia setata pe share.

### 4.5 Previous Versions (shadow copies)

- **Detectare** a filesystem-ului pe care sta share-ul:
  - pe ZFS: `.zfs/snapshot` + `shadow_copy2` cu formatul de nume al tool-ului de snapshot;
  - pe Btrfs: snapper + `vfs_snapper`, sau un subvolum `.snapshots` cu `shadow_copy2`.
- **Programul de snapshot-uri:**
  - v1 expune snapshot-urile care exista deja;
  - optional, plugin-ul configureaza un program (sanoid pe ZFS, snapper pe Btrfs) cu retentie orara / zilnica / saptamanala.
- Pe alte filesystem-uri (ext4/XFS fara LVM thin), optiunea e dezactivata si UI-ul explica de ce.

### 4.6 Active Directory

- **Wizard de join:**
  1. verificari DNS / SRV / ceas;
  2. `realm join` sau `net ads join` cu winbind;
  3. alegerea idmap (`rid` implicit, `ad` daca domeniul are RFC2307);
  4. `winbind use default domain`, template shell / homedir;
  5. configurarea NSS si PAM (`libnss-winbind`, `libpam-winbind`).
- **In mod AD:**
  - tab-ul Users afiseaza userii si grupurile din domeniu (`wbinfo`), read-only; managementul de parole locale e dezactivat pentru ei;
  - picker-ele de acces au search pe useri si grupuri AD;
  - FTP autentifica userii AD prin PAM winbind;
  - home-urile AD vin din template (`/home/%D/%U`), create automat la prima logare (`pam_mkhomedir`).
- NFS cu Kerberos (`sec=krb5p`) ramane pentru o versiune ulterioara. In v1, NFS-ul cu AD foloseste `sec=sys`, cu UID-uri consistente prin idmap `rid`.
- Leave domain, cu confirmare dubla.

### 4.7 Useri si grupuri (mod local)

- **Useri:** creare (shell nologin implicit), editare (nume complet, grupuri, shell), setare parola sincronizata, enable/disable SMB. Stergerea userilor ramane in Cockpit Accounts, ca sa nu dublam.
- **Grupuri:** creare, membri, stergere (doar daca nu sunt folosite in share-uri).
- **Avertismente:**
  - un user folosit intr-un share, dar fara cont Samba, primeste un badge;
  - UI-ul spune clar ca o parola schimbata din Cockpit Accounts nu ajunge si in Samba.

### 4.8 Overview / monitorizare

- **Servicii:** status pentru smbd, nmbd, winbind, nfs-server, proftpd, avahi si wsdd, cu start/restart.
- **Conexiuni active:**
  - SMB: `smbstatus --json`, cu sesiuni, fisiere deschise si deconectare sesiune;
  - NFS: clienti conectati;
  - FTP: `ftpwho`.
- **Firewall:** detectie ufw / firewalld, cu buton care deschide doar porturile protocoalelor active (445, 2049, 21 + intervalul pasiv, 5353 mDNS, 3702 wsdd).

## 5. Interfata

Interfata are cinci tab-uri: **Overview / Shares / Users & Groups / Settings / Active Directory**, plus wizard-ul de migrare la prima rulare.

- **Controale:** custom (multiselect cu search, switch-uri, browser de foldere); fara `type=number` si fara select-uri native.
- **Aspect:** aliniat vizual cu Cockpit (PatternFly 5). Light/dark urmeaza tema Cockpit (`shell:style`) si `prefers-color-scheme`.
- **Ajutor contextual:** fiecare optiune Samba are un tooltip care explica ce face si ce risc are.
- **Fara build-chain Node:** JS si CSS statice, ca pachetul sa ramana simplu de intretinut.

## 6. Livrare

- **Pachet:** `.deb` arch `all`, versionat semver, cu `debian/changelog`.
  - `Depends`: cockpit, python3, samba, nfs-kernel-server, proftpd-core, proftpd-mod-crypto, avahi-daemon;
  - `Recommends`: wsdd, realmd, adcli, winbind, libnss-winbind, libpam-winbind, krb5-user;
  - `Suggests`: sanoid, snapper.
- **postinst / postrm:**
  - postinst configureaza include-ul ProFTPD si mod_tls, fara sa suprascrie config-urile clientului;
  - postrm curata bind mount-urile si fragmentele generate, dar pastreaza `state.json`.
- **Build:** un script reproductibil (`dpkg-buildpackage`) si un pipeline CI care construieste si ruleaza testele pe VM-uri.
- **Distributie (faza finala):** repo apt semnat GPG (reprepro sau aptly) pe un server QM / StratusPro, plus release-uri pe GitHub (.deb atasat).
- **Licenta:** GPL-3.0-or-later. Fiecare fisier sursa are header SPDX. Contributiile externe se accepta cu DCO (`Signed-off-by`).

## 7. Testare

- **Automat** (VM-uri Ubuntu 22.04 / 24.04 cu systemd, de exemplu pe Proxmox):
  - acces pe share-uri: smbclient, mount.cifs, mount NFS v3/v4, ftplib cu FTPS;
  - permisiuni negative (userul neautorizat e refuzat);
  - rollback la config invalid;
  - migrarea de pe 45Drives.
- **Manual** (checklist):
  - Mac: Finder, Time Machine pe un backup real;
  - Windows 11: Previous Versions, descoperire in retea, ACL-uri;
  - AD: join pe un domeniu de test.

## 8. Faze

| Faza | Continut | Rezultat livrabil |
|---|---|---|
| 0 | Refactor pe modelul state + reconciliere, backup/rollback, migrare 45Drives | backend nou, testat |
| 1 | UI nou: Shares (SMB/NFS/FTP), Users & Groups local, Samba global de baza, home-uri | v0.5 utilizabil intern |
| 2 | Profile macOS / Windows, Time Machine, Recycle bin, Avahi + wsdd | v0.7 |
| 3 | Previous Versions (ZFS/Btrfs) + program de snapshot-uri | v0.8 |
| 4 | Active Directory (join, picker AD, FTP prin PAM winbind) | v0.9 |
| 5 | Overview: servicii, conexiuni, firewall | v0.95 |
| 6 | Pachet .deb, CI, repo apt, documentatie de instalare pentru clienti | v1.0 |

## 9. Intrebari deschise

1. **Nume si branding:** "ShareHub" e doar nume de lucru. Il vrei neutru sau cu brand StratusPro / QM?
2. **Distributii suportate:** doar Ubuntu 24.04, sau si 22.04 / Debian 12? Versiunile de Samba difera (4.15 / 4.17 / 4.19), iar asta conteaza la fruit si Time Machine.
3. **Snapshot-uri:** in v1 doar expunem snapshot-urile existente, sau plugin-ul configureaza si programul (sanoid / snapper)?
4. **AD:** clientii tinta au atribute RFC2307 (uidNumber) in AD? Asta decide intre idmap `ad` si `rid`.
5. ~~**Licenta**~~ - decis: GPL-3.0-or-later, repo public.
6. **Mediu de test:** am acces la un Proxmox / VM pentru testele cu systemd, un Mac si un domeniu AD de test, sau le rulezi tu dupa checklist?
