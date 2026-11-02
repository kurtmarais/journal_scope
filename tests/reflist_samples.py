"""Small samples in the layout of each reference list."""

from pathlib import Path

from openpyxl import Workbook

def crest_xlsx(path: Path) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["DHET-approved journals 2026"])
    ws.append(["Journal", "Publisher", "ISSN", "eISSN", "Journal ID", "Status", "Reason"])
    ws.append(["Journal of Biomedical Informatics", "Elsevier", "1532-0464", "1532-0480", "JNL_014249", "Eligible for subsidy", None])
    ws.append(["Journal of Biomedical Informatics: X", "Academic Press", "2590-177X", None, "JNL_024590", "Discontinued",
               "The journal ceased publication in 2021."])
    wb.save(path)
    return path.read_bytes()


def sanlic_xlsx(path: Path) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Note: indicative only"])
    ws.append(["Journal Title", "eISSN", "hybrid_gold_oa", "Included in R&P agreement", "Explanation if not included", "Subject", "Publisher"])
    ws.append(["Journal of Biomedical Informatics", "1532-0464", "Hybrid", "Y", None, None, "ELSEVIER"])
    ws.append(["JMIR Medical Informatics", "2291-9694", "Gold", "N", "Not part of any agreement", None, "JMIR"])
    wb.save(path)
    return path.read_bytes()


def get_bytes_for(tmp: Path):
    crest = crest_xlsx(tmp / "c.xlsx")
    sanl = sanlic_xlsx(tmp / "s.xlsx")
    pages = {
        "https://sanlic.ac.za/read-and-publish-agreements/":
            b'<a href="https://sanlic.ac.za/wp-content/uploads/2026/03/Alphabetical-RP-Journal-Title-List_2026.xlsx">x</a>',
        "https://sanlic.ac.za/wp-content/uploads/2026/03/Alphabetical-RP-Journal-Title-List_2026.xlsx": sanl,
        "https://db.crest.sun.ac.za/zapublications/":
            b'<a href="https://db.crest.sun.ac.za/zapublications/downloadfile/downloadable_journal_master_list_20260811.xlsx">Download</a>',
        "https://db.crest.sun.ac.za/zapublications/downloadfile/downloadable_journal_master_list_20260811.xlsx": crest,
    }
    return lambda url: pages[url]
