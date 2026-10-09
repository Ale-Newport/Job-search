"""Synthetic networking UI test. External navigation is intercepted; nobody is messaged."""
import asyncio
import json
from playwright.async_api import async_playwright, expect


async def main():
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='chrome',headless=True)
        page=await browser.new_page(viewport={'width':1440,'height':1080})
        headers={'Authorization':'Bearer meridian-development-token'}
        job=await (await page.request.post('http://127.0.0.1:8765/api/jobs',headers=headers,data={'title':'Software Engineer','company':'Networking Fixture','url':'https://networking-fixture.invalid/job'})).json()
        await page.request.post('http://127.0.0.1:8765/api/facts',headers=headers,data={'category':'project','key':'networking fixture','value':'Built a verified Python pipeline.','verification_status':'verified'})
        await page.goto('http://127.0.0.1:1420')
        await page.get_by_role('navigation').get_by_role('button',name='Networking',exact=True).click()
        await expect(page.get_by_role('heading',name='Networking',exact=True)).to_be_visible()
        await page.get_by_role('combobox',name='Related opportunity').select_option(job['id'])
        await page.get_by_role('button',name='Add contact',exact=True).click()
        for label,value in [('Full Name','Synthetic Recruiter'),('Company','Networking Fixture'),('Role','Graduate recruiter'),('Profile Url','https://www.linkedin.com/in/meridian-synthetic-fixture'),('Source','Synthetic verified recruiting fixture'),('Evidence of recruiting responsibility','Graduate recruitment post')]:
            await page.get_by_role('textbox',name=label,exact=True).fill(value)
        await page.get_by_role('checkbox',name='I verified the current role and source').check()
        await page.get_by_role('button',name='Save contact',exact=True).click()
        await page.get_by_role('row').filter(has_text='Synthetic Recruiter').get_by_role('button',name='Open contact').click()
        await page.get_by_role('button',name='Generate message for selected role').click()
        await expect(page.get_by_role('textbox',name='Review message')).to_contain_text('Python pipeline')
        await page.evaluate("""() => {
            window.fixtureOpened=[]; window.open=url=>window.fixtureOpened.push(url);
            Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.fixtureCopy=text},configurable:true});
        }""")
        await page.get_by_role('button',name='Approve & continue to LinkedIn').click()
        await expect(page.get_by_text('Reviewed message copied. Complete the send on LinkedIn, then confirm below.')).to_be_visible()
        handoff=await page.evaluate('({urls:window.fixtureOpened,copy:window.fixtureCopy})')
        assert handoff['urls']==['https://www.linkedin.com/in/meridian-synthetic-fixture']
        assert 'Python pipeline' in handoff['copy']
        # Handoff must not silently mark a message sent.
        contacts=await (await page.request.get('http://127.0.0.1:8765/api/networking/contacts',headers=headers)).json()
        contact=next(c for c in contacts['items'] if c['full_name']=='Synthetic Recruiter')
        assert contact['messages'][0]['state']=='MESSAGE_PREPARED'
        await page.get_by_role('button',name='I sent this message').click()
        await expect(page.get_by_role('button',name='I sent this message')).to_be_disabled()
        print(json.dumps({'result':'passed','checked':'verified contact, grounded editable draft, exact destination and clipboard handoff, explicit recording, no external message'}))
        await browser.close()


if __name__=='__main__':
    asyncio.run(main())
