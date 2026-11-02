"""Small samples in the layout of each reference list."""

from pathlib import Path

from openpyxl import Workbook

SCIMAGO_CSV = (
    'Rank;Sourceid;Title;Type;Issn;SJR;SJR Best Quartile;H index;Total Docs. (2025);Total Docs. (3years);'
    'Total Refs.;Total Citations (3years);Citable Docs. (3years);Cites / Doc. (2years);Ref. / Doc.;%Female;'
    'Overton;SDG;Country;Region;Publisher;Coverage;Categories;Areas\n'
    '1;23706;"Journal of Biomedical Informatics";journal;"15320464, 15320480";1,320;Q1;145;199;600;9000;4000;575;'
    '6,551;45,2;38,27;1;20;United States;Northern America;"Academic Press Inc.";"2001-2026";'
    '"Computer Science Applications (Q1); Health Informatics (Q1)";"Computer Science; Medicine"\n'
    '2;21101030139;"JMIR Medical Informatics";journal;"22919694";0,950;Q2;60;300;800;9000;3000;700;'
    '3,100;40,0;45,00;0;10;Canada;Northern America;"JMIR Publications Inc.";"2013-2026";'
    '"Health Informatics (Q2)";"Medicine"\n'
    '3;99999;"Journal of Biomedical Discontinued";journal;"12345679";;-;3;0;0;0;0;0;0;0;0;0;0;Spain;Western Europe;'
    '"Old Press";"1990-2010";"Medicine (miscellaneous)";"Medicine"\n'
).encode("utf-8")


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
