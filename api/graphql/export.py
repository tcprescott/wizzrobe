"""Print the schema: ``poetry run python -m api.graphql.export > docs/reference/schema.graphql``."""

from api.graphql.schema import schema

if __name__ == '__main__':
    print(schema.as_str())
