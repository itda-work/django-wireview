import time

import pytest
from django.test import Client, TestCase
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


def expect_text_eventually(locator, expected: str, timeout: float = 5.0):
    """Wait for locator to have expected text content."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        actual = locator.inner_text().strip()
        if actual == expected:
            return True
        time.sleep(0.1)
    actual = locator.inner_text().strip()
    assert actual == expected, f"Expected '{expected}', got '{actual}'"


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
        page.wait_for_selector('[data-name=XTodoList][data-is-live="true"]', timeout=5000)

        # Get input element
        new_item_input = page.locator("input.new-todo")

        # Add first task
        new_item_input.fill("HI")
        new_item_input.press("Enter")

        # Wait for item to appear
        page.wait_for_selector("[data-name=XTodoItem]", timeout=5000)

        # Check counter
        counter = page.locator("[data-name=XTodoCounter]")
        expect_text_eventually(counter, "1 item left")

        # Check first item label
        todo_item = page.locator("[data-name=XTodoItem]").first
        first_label = todo_item.locator("label")
        expect_text_eventually(first_label, "HI")

        # Add second task
        new_item_input.fill("Second task")
        new_item_input.press("Enter")

        # Wait and check counter
        expect_text_eventually(counter, "2 items left")

        # Check both items
        todo_items = page.locator("[data-name=XTodoItem]")
        assert todo_items.count() >= 2
        expect_text_eventually(todo_items.nth(0).locator("label"), "HI")
        expect_text_eventually(todo_items.nth(1).locator("label"), "Second task")

        # Mark second task as done
        second_task = todo_items.nth(1)
        second_task.locator("[name=completed]").click()

        # Wait for completed class
        page.wait_for_selector("li.completed", timeout=5000)
        assert counter.inner_text().strip() == "1 item left"

        # Show active items (using CSS selector since these are <a> without href)
        page.locator(".filters a", has_text="Active").click()
        page.wait_for_timeout(300)
        assert second_task.locator("li.hidden").count() > 0

        first_task = todo_items.nth(0)
        first_task_classes = first_task.locator("li").first.get_attribute("class") or ""
        assert "hidden" not in first_task_classes

        # Show completed items
        page.locator(".filters a", has_text="Completed").click()
        page.wait_for_timeout(300)
        assert first_task.locator("li.hidden").count() > 0
        second_task_classes = second_task.locator("li").first.get_attribute("class") or ""
        assert "hidden" not in second_task_classes

        # Show all
        page.locator(".filters a", has_text="All").click()
        page.wait_for_timeout(300)
        assert page.locator("li.hidden").count() == 0

        # Clear completed tasks
        page.locator("button.clear-completed").click()
        page.wait_for_timeout(300)

        items = page.locator("[data-name=XTodoItem]")
        assert items.count() == 1
        assert items.first.get_attribute("id") == first_task.get_attribute("id")

        # Edit the first task
        first_task.locator("label").click()
        page.wait_for_timeout(300)

        first_task_input = first_task.locator("input.edit")
        first_task_input.wait_for(state="visible")

        # Select all and replace (use Meta+a for macOS)
        first_task_input.press("Meta+a")
        first_task_input.fill("First item")
        first_task_input.press("Enter")
        page.wait_for_timeout(300)

        # Check editing mode is gone
        assert page.locator("li.editing").count() == 0

        # Delete the task - hover to show destroy button
        first_task.hover()
        first_task.locator(".destroy").click()
        page.wait_for_timeout(300)

        # Check no items left
        assert page.locator("[data-name=XTodoItem]").count() == 0
