import asyncio
import shutil
import threading
import time
import unittest
from os import environ as env
from random import randint
from urllib.parse import urljoin
from time import sleep

from uvicorn.main import Server as Uvicorn
from uvicorn.config import Config as UvicornConfig

from django.test import TestCase, TransactionTestCase, Client, override_settings
from channels.routing import get_default_application

from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from splinter import Browser
from splinter.config import Config

# Check if geckodriver is available for Selenium tests
GECKODRIVER_AVAILABLE = shutil.which("geckodriver") is not None
from splinter.element_list import ElementList
from splinter.driver import ElementAPI
from splinter.driver.webdriver import WebDriverElement
from splinter.driver.webdriver.firefox import WebDriver as FirefoxDriver
from splinter.driver.lxmldriver import LxmlDriver
from splinter.driver.djangoclient import DjangoClient as DjangoDriver

from .models import Item


class TestNormalRendering(TestCase):

    def setUp(self):
        Item.objects.create(text='First task')
        Item.objects.create(text='Second task')
        self.c = Client()

    def test_everything_two_tasks_are_rendered(self):
        response = self.c.get('/todo')
        assert response.status_code == 200
        self.assertContains(response, 'First task')
        self.assertContains(response, 'Second task')


# HACK: https://github.com/cobrateam/splinter/pull/820

def submit(self: LxmlDriver, form):
    method = form.attrib.get("method", "get").lower()
    action = form.attrib.get("action", "")
    if action.strip() not in (".", ""):
        url = urljoin(self._url, action)
    else:
        url = self._url
    self._url = url
    data = self.serialize(form)

    self._do_method(method, url, data=data)
    return self._response


LxmlDriver.submit = submit


class UvicornThread(threading.Thread):

    def __init__(self, application, host, port):
        super().__init__()
        self.host = host
        self.port = port
        self.application = application
        self.server = None

    @override_settings(DEBUG=True)
    def run(self):
        self.loop = asyncio.new_event_loop()
        config = UvicornConfig(self.application, host=self.host, port=self.port)
        self.server = Uvicorn(config)
        self.server.install_signal_handlers = lambda *args, **kwargs: None
        self.loop.run_until_complete(self.server.serve())
        self.server = None

    @property
    def started(self):
        return self.server and self.server.started

    def terminate(self):
        self.server.force_exit = True
        self.server.should_exit = True
        self.loop.create_task(self.server.shutdown())


class ChannelsLiveServerTestCase(TransactionTestCase):
    """Live server test case using Uvicorn and Splinter/Selenium."""

    host = '127.0.0.1'
    driver_type = 'firefox'
    x: FirefoxDriver

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        headless = not env.get('DISPLAY')
        config = Config(headless=headless)
        cls.x = Browser(
            cls.driver_type,
            config=config,
            wait_time=10 if headless else 2
        )

    @classmethod
    def tearDownClass(cls):
        cls.x.quit()
        super().tearDownClass()

    @property
    def live_server_url(self):
        return "http://%s:%s" % (self.host, self._port)

    def scroll_into_view(self, element):
        if isinstance(element, ElementList):
            element = element[0]

        if isinstance(element, ElementAPI):
            element = element._element

        self.x.execute_script('arguments[0].scrollIntoView(true);', element)

    def setUp(self):
        super().setUp()
        self._port = randint(9000, 40000)
        self._server = UvicornThread(
            get_default_application(),
            self.host,
            self._port,
        )
        self._server.start()
        while not self._server.started:
            sleep(0.1)

    def tearDown(self):
        self._server.terminate()
        super().tearDown()

    def assert_focused(self, element):
        return element._element == self.x.driver.switch_to.active_element

    def chain(self):
        return ActionChains(self.x.driver)

    def send_ctrl(self, key):
        (
            self.chain()
            .key_down(Keys.CONTROL)
            .send_keys(key)
            .key_up(Keys.CONTROL)
            .perform()
        )

    def assert_text_eventually(self, element, expected, timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if element.text == expected:
                return True
            sleep(0.1)
        assert element.text == expected

    def print_html(self):
        from pygments import highlight
        from pygments.lexers import HtmlLexer
        from pygments.formatters import TerminalTrueColorFormatter
        print(highlight(
            self.x.html,
            HtmlLexer(),
            TerminalTrueColorFormatter()
        ))


@unittest.skipUnless(GECKODRIVER_AVAILABLE, "geckodriver not found")
class SeleniumTests(ChannelsLiveServerTestCase):

    def test_click(self):
        # Navigate to todo app
        self.x.visit(self.live_server_url)
        self.x.links.find_by_text('todo view').click()

        # Wait for WebSocket connection
        assert self.x.is_element_present_by_css(
            '[data-name=XTodoList][data-is-live="true"]',
            wait_time=5,
        )

        new_item_input = self.x.find_by_tag('input')[0]
        # Add first task
        new_item_input._element.send_keys(f'HI{Keys.ENTER}')
        assert self.x.is_element_present_by_css(
            '[data-name=XTodoItem]', wait_time=5
        )
        assert not new_item_input.text
        counter = self.x.find_by_css('[data-name=XTodoCounter]')
        self.assert_text_eventually(counter, '1 item left')
        todo_item = self.x.find_by_css('[data-name=XTodoItem]')
        first_label = todo_item.find_by_tag('label')
        self.assert_text_eventually(first_label, 'HI')

        # Add second task
        new_item_input._element.send_keys(f'Second task{Keys.ENTER}')
        assert self.x.is_element_present_by_css(
            '[data-name=XTodoItem]', wait_time=5
        )
        self.assert_text_eventually(counter, '2 items left')
        todo_items = self.x.find_by_css('[data-name=XTodoItem]')
        assert len(todo_items) >= 2
        self.assert_text_eventually(todo_items[0].find_by_tag('label'), 'HI')
        self.assert_text_eventually(
            todo_items[1].find_by_tag('label'), 'Second task'
        )

        # Mark second task as done
        second_task: WebDriverElement = todo_items[1]
        second_task.find_by_css('[name=completed]').click()
        assert self.x.is_element_present_by_css('li.completed', wait_time=5)
        assert counter.text == '1 item left'

        # Show active items
        self.x.links.find_by_partial_text('Active').click()
        sleep(0.3)
        assert second_task.find_by_css('li.hidden')
        first_task: WebDriverElement = todo_items[0]
        assert not first_task.find_by_css('li').has_class('hidden')

        # Show completed items
        self.x.links.find_by_partial_text('Completed').click()
        sleep(0.3)
        assert first_task.find_by_css('li.hidden')
        assert not second_task.find_by_css('li').has_class('hidden')

        # Show all
        self.x.links.find_by_partial_text('All').click()
        sleep(0.3)
        assert self.x.is_element_not_present_by_css('li.hidden')

        # Clear completed tasks
        self.x.find_by_css('button.clear-completed').click()
        sleep(0.3)
        items = self.x.find_by_css('[data-name=XTodoItem]')
        assert len(items) == 1
        assert items['id'] == first_task['id']

        # Edit the first task.
        first_task.find_by_tag('label').click()
        sleep(0.3)
        first_task_input = first_task.find_by_css('input.edit')
        assert self.assert_focused(first_task_input)
        self.send_ctrl('a')
        first_task_input._element.send_keys(
            f'{Keys.BACKSPACE}First item{Keys.ENTER}'
        )
        sleep(0.3)

        assert self.x.is_element_not_present_by_css('li .editing')
        first_task.find_by_css('.destroy').click()
        sleep(0.3)
        assert self.x.is_element_not_present_by_css('[data-name=XTodoItem]')
