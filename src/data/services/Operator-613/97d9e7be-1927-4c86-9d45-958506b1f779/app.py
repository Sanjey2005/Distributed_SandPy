class Task:
    def __init__(self, title, description='', category='General'):
        self.title = title
        self.description = description
        self.category = category
        self.completed = False

    def edit(self, title=None, description=None, category=None):
        if title:
            self.title = title
        if description:
            self.description = description
        if category:
            self.category = category

    def mark_completed(self):
        self.completed = True

    def __str__(self):
        status = '✓' if self.completed else '✗'
        return f"[{status}] {self.title} - {self.description} (Category: {self.category})"


class TodoApp:
    def __init__(self):
        self.tasks = []

    def add_task(self, title, description='', category='General'):
        new_task = Task(title, description, category)
        self.tasks.append(new_task)

    def edit_task(self, index, title=None, description=None, category=None):
        if 0 <= index < len(self.tasks):
            self.tasks[index].edit(title, description, category)

    def delete_task(self, index):
        if 0 <= index < len(self.tasks):
            del self.tasks[index]

    def list_tasks(self):
        for index, task in enumerate(self.tasks):
            print(f"{index}: {task}")

    def mark_task_completed(self, index):
        if 0 <= index < len(self.tasks):
            self.tasks[index].mark_completed()


# Example usage
if __name__ == "__main__":
    app = TodoApp()
    app.add_task("Buy groceries", "Milk, Bread, Eggs", "Shopping")
    app.add_task("Read a book", "Finish reading '1984'", "Leisure")
    app.list_tasks()

    app.edit_task(0, category="Errands")
    app.mark_task_completed(1)
    app.list_tasks()

    app.delete_task(0)
    app.list_tasks()