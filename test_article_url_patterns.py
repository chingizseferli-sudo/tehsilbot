import importlib.util
import os
import sys
import types


os.environ.setdefault("SUPABASE_URL", "")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "")


if "requests" not in sys.modules:
    requests = types.ModuleType("requests")

    class _RequestException(Exception):
        pass

    class _Timeout(_RequestException):
        pass

    class _SSLError(_RequestException):
        pass

    class _ConnectionError(_RequestException):
        pass

    requests.Timeout = _Timeout
    requests.exceptions = types.SimpleNamespace(
        SSLError=_SSLError,
        ConnectionError=_ConnectionError,
        RequestException=_RequestException,
    )
    requests.get = lambda *_args, **_kwargs: None
    requests.post = lambda *_args, **_kwargs: None
    requests.patch = lambda *_args, **_kwargs: None
    sys.modules["requests"] = requests


if "feedparser" not in sys.modules:
    feedparser = types.ModuleType("feedparser")
    feedparser.parse = lambda *_args, **_kwargs: types.SimpleNamespace(entries=[], bozo=False)
    sys.modules["feedparser"] = feedparser


if "bs4" not in sys.modules:
    bs4 = types.ModuleType("bs4")

    class _BeautifulSoup:
        def __init__(self, *_args, **_kwargs):
            pass

        def find(self, *_args, **_kwargs):
            return None

        def find_all(self, *_args, **_kwargs):
            return []

        def select(self, *_args, **_kwargs):
            return []

    bs4.BeautifulSoup = _BeautifulSoup
    sys.modules["bs4"] = bs4


spec = importlib.util.spec_from_file_location("site_monitor", "site_monitor.py")
site_monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(site_monitor)


def assert_accepted(page_url, link):
    results = []
    site_monitor.add_item(
        results,
        page_url,
        "Universitetdə elmi tədbir və yeni proqram təqdim edilib",
        link,
        [],
    )
    assert len(results) == 1, link


def assert_rejected(page_url, link):
    results = []
    site_monitor.add_item(
        results,
        page_url,
        "Universitet xəbərləri və yenilikləri siyahısı",
        link,
        [],
    )
    assert results == [], link


def main():
    accepted = [
        ("https://adalet.az", "https://adalet.az/az/posts/detail/milli-meclisin-novbeti-iclasi-1782384545"),
        ("https://adalet.az", "https://adalet.az/az/writers/detail/kose-yazisi-1782384545"),
        ("https://asoiu.edu.az/allNews", "https://asoiu.edu.az/single_news/3992"),
        ("https://bdu-qazax.edu.az/index.php/az/kheberler", "https://bdu-qazax.edu.az/index.php/az/kheberler/425-maarif26"),
        ("https://www.atu.edu.az/xeberler/1", "https://www.atu.edu.az/xeber/1505"),
        ("https://gsaz.az/articles", "https://gsaz.az/articles/view/16/EKITABLAR"),
        ("https://afn.az", "https://afn.az/173683-prezidentden-silahli-quvveler-gunu-ile-bagli-paylasim.html"),
        ("https://big.az", "https://big.az/587466-tramp-ve-sepah-hedeleyir-hormuz-duyunu.html"),
        ("https://editor.az", "https://editor.az/pasinyan-azerbaycanla-bagli-sehve-yol-vermisem/"),
        ("https://muallim.edu.az", "https://muallim.edu.az/miq-imtahanlarinin-vaxti-aciqlandi"),
        ("https://busaat.az", "https://busaat.az/azerbaycan-silahli-quvvelerinin-yaranma-gunudur"),
        ("https://yenicag.az", "https://yenicag.az/azerbaycanda-hebsde-evlenenlerin-sayi-aciqlandi"),
        ("https://embawood.az/news", "https://embawood.az/blog/tezlikle-yeni-model-calessa"),
        ("https://deazmed.az", "https://deazmed.az/az/melumat-ve-xeberler/fintiba-hesabindan-almaniyada-istifad-edilmsi-entsperrung"),
        ("https://www.bqu.edu.az", "https://www.bqu.edu.az/announcement_single/47"),
        ("https://www.airport.az/press-release", "https://www.airport.az/en/press-release/flyone-asia-launches-scheduled-flights-to-baku-and-ganja-airports/"),
        ("https://ayna.az", "https://ayna.az/ruhani-rejim-bohran-icinde-mollokratiyanin-islam-nizaminin-esl-simasi"),
        ("https://ekosu.az", "https://ekosu.az/hidrotexniki-qurgular-barede-yeni-kitab-isiq-uzu-gorub/"),
        ("https://www.aem.az", "https://www.aem.az/elmi-qaynaqlar-xix-respublika-elmi-konfransi"),
    ]
    rejected = [
        ("https://asoiu.edu.az", "https://asoiu.edu.az/allNews"),
        ("https://bdu-qazax.edu.az", "https://bdu-qazax.edu.az/index.php/az/kheberler"),
        ("https://bmtk.edu.az", "https://bmtk.edu.az/kateqoriya/xeberler/"),
        ("https://gsaz.az", "https://gsaz.az/articles"),
        ("https://gsaz.az", "https://gsaz.az/articles/category/5"),
        ("https://edusinaq.az", "https://edusinaq.az/education/"),
        ("https://example.az", "https://example.az/about.html"),
        ("https://editor.az", "https://editor.az/elaqe"),
        ("https://editor.az", "https://editor.az/privacy-policy"),
        ("https://editor.az", "https://editor.az/category/siyaset"),
        ("https://muallim.edu.az", "https://muallim.edu.az/elaqe"),
        ("https://example.az", "https://example.az/azerbaycanda-hebsde-evlenenlerin-sayi-aciqlandi"),
        ("https://embawood.az", "https://embawood.az/blog/"),
        ("https://example.az", "https://example.az/blog/real-looking-article-title"),
        ("https://example.az", "https://example.az/announcement_single/47"),
        ("https://example.az", "https://example.az/en/press-release/real-looking-title"),
        ("https://deazmed.az", "https://deazmed.az/az/melumat-ve-xeberler/"),
        ("https://www.bqu.edu.az", "https://www.bqu.edu.az/announcement_single/"),
        ("https://www.airport.az", "https://www.airport.az/en/press-release/"),
        ("https://ayna.az", "https://ayna.az/yazarlar/qulu-meherremli"),
        ("https://ekosu.az", "https://ekosu.az/qaydalar/"),
        ("https://www.aem.az", "https://www.aem.az/elaqe"),
        ("https://example.az", "https://example.az/archive/2026"),
        ("https://example.az", "https://example.az/tag/tehsil"),
        ("https://example.az", "https://example.az/search?q=tehsil"),
    ]

    for page_url, link in accepted:
        assert site_monitor.is_article_like_link(link), link
        assert_accepted(page_url, link)

    for page_url, link in rejected:
        assert_rejected(page_url, link)

    print("article url pattern fixtures ok")


if __name__ == "__main__":
    main()
