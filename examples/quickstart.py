"""Create a project and submit its first run against a local Kionga gateway."""

from kionga import KiongaClient


with KiongaClient() as client:
    project = client.create_project(
        "customer-churn",
        description="Predict customers likely to leave in the next 30 days",
        template="tabular-classification",
    )
    run = client.submit_pipeline(project.id)
    print(f"Created {project.name}; run {run.id} is {run.status}.")
