import urllib.request
from bs4 import BeautifulSoup
import json

html = urllib.request.urlopen('https://www.projectpluto.com/fo.htm').read().decode('utf-8')
soup = BeautifulSoup(html, 'html.parser')
form = soup.find('form')
if form:
    print("Form action:", form.get('action'))
    print("Form method:", form.get('method'))
    for input_tag in form.find_all(['input', 'textarea', 'select']):
        print(f"{input_tag.name}: name={input_tag.get('name')} type={input_tag.get('type')} value={input_tag.get('value')}")
