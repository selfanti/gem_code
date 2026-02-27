from trafilatura import fetch_url, extract
url=input()
downloaded=fetch_url(url)
result = extract(downloaded,output_format="markdown",include_comments=False)
print(result)