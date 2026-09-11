import pytest
from django.test import Client, TestCase
from testproj.e2e_browser import expect_count, expect_text, wait_live
from testproj.e2e_server import serve

from .models import Item


class TestNormalRendering(TestCase):
    def setUp(self):
        Item.objects.create(text="First task")
        Item.objects.create(text="Second task")
        self.c = Client()

    def test_everything_two_tasks_are_rendered(self):
        response = self.c.get("/todo")
        assert response.status_code == 200
        self.assertContains(response, "First task")
        self.assertContains(response, "Second task")


@pytest.fixture(scope="function")
def wireview_server():
    """Fixture that starts a live ASGI server for E2E tests."""
    with serve() as base_url:
        yield base_url


@pytest.mark.e2e
class TestPlaywrightTodo:
    """E2E tests for Todo app using Playwright."""

    @pytest.fixture(autouse=True)
    def setup_db(self, transactional_db):
        """Ensure database is available for tests."""
        pass

    def test_click(self, page, wireview_server):
        """Test complete Todo app workflow."""
        # Navigate to todo app
        page.goto(wireview_server)
        page.get_by_role("link", name="Launch Todo App").click()

        # Wait for WebSocket connection
        wait_live(page, '[data-name=XTodoList][data-is-live="true"]')

        # Get input element
        new_item_input = page.locator("input.new-todo")

        # Add first task
        new_item_input.fill("HI")
        new_item_input.press("Enter")

        # Wait for item to appear
        expect_count(page.locator("[data-name=XTodoItem]"), 1)

        # Check counter
        counter = page.locator("[data-name=XTodoCounter]")
        expect_text(counter, "1 item left")

        # Check first item label
        todo_item = page.locator("[data-name=XTodoItem]").first
        first_label = todo_item.locator("label")
        expect_text(first_label, "HI")

        # Add second task
        new_item_input.fill("Second task")
        new_item_input.press("Enter")

        # Wait and check counter
        expect_text(counter, "2 items left")

        # Check both items
        todo_items = page.locator("[data-name=XTodoItem]")
        expect_count(todo_items, 2)
        expect_text(todo_items.nth(0).locator("label"), "HI")
        expect_text(todo_items.nth(1).locator("label"), "Second task")

        # Mark second task as done
        second_task = todo_items.nth(1)
        second_task.locator("[name=completed]").click()

        # Wait for completed class
        expect_count(page.locator("li.completed"), 1)
        expect_text(counter, "1 item left")

        first_task = todo_items.nth(0)

        # Show active items (using CSS selector since these are <a> without href)
        page.locator(".filters a", has_text="Active").click()
        expect_count(second_task.locator("li.hidden"), 1)
        expect_count(first_task.locator("li.hidden"), 0)

        # Show completed items
        page.locator(".filters a", has_text="Completed").click()
        expect_count(first_task.locator("li.hidden"), 1)
        expect_count(second_task.locator("li.hidden"), 0)

        # Show all
        page.locator(".filters a", has_text="All").click()
        expect_count(page.locator("li.hidden"), 0)

        # Clear completed tasks
        page.locator("button.clear-completed").click()

        items = page.locator("[data-name=XTodoItem]")
        expect_count(items, 1)
        assert items.first.get_attribute("id") == first_task.get_attribute("id")

        # Edit the first task
        first_task.locator("label").click()

        first_task_input = first_task.locator("input.edit")
        first_task_input.wait_for(state="visible")

        # Select all and replace (use Meta+a for macOS)
        first_task_input.press("Meta+a")
        first_task_input.fill("First item")
        first_task_input.press("Enter")

        # Check editing mode is gone
        expect_count(page.locator("li.editing"), 0)

        # Delete the task - hover to show destroy button
        first_task.hover()
        first_task.locator(".destroy").click()

        # Check no items left
        expect_count(page.locator("[data-name=XTodoItem]"), 0)
