FROM maven:3.9.9-eclipse-temurin-21 AS build

WORKDIR /workspace
COPY stock-service/pom.xml ./pom.xml
RUN mvn -B -DskipTests dependency:go-offline

COPY stock-service/src ./src
RUN mvn -B -DskipTests package

FROM eclipse-temurin:21-jre

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=build /workspace/target/*.jar /app/app.jar

EXPOSE 8081 8082
ENTRYPOINT ["java", "-jar", "/app/app.jar"]
