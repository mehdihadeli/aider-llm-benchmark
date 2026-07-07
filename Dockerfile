FROM buildpack-deps:jammy

# Install Python 3.11 and .NET 10 SDK for the Python-based harness and bundled C# exercises.
# The bundled C# projects target net10.0, but the Ubuntu 22.04 apt feed does not publish
# dotnet-sdk-10.0, so install it via the official dotnet-install script instead.
RUN apt-get update && apt-get install -y \
    software-properties-common \
    wget \
    gnupg \
    ca-certificates \
    python-is-python3 \
    && add-apt-repository ppa:deadsnakes/ppa \
    && wget https://packages.microsoft.com/config/ubuntu/22.04/packages-microsoft-prod.deb -O /tmp/packages-microsoft-prod.deb \
    && dpkg -i /tmp/packages-microsoft-prod.deb \
    && rm /tmp/packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y \
    python3.11 \
    python3.11-venv \
    python3.11-dev \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Make python3.11 the default python3
RUN update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

RUN wget https://dot.net/v1/dotnet-install.sh -O /tmp/dotnet-install.sh \
    && bash /tmp/dotnet-install.sh --channel 10.0 --install-dir /usr/share/dotnet \
    && ln -s /usr/share/dotnet/dotnet /usr/bin/dotnet \
    && rm /tmp/dotnet-install.sh

COPY . /aider
RUN pip3 install --no-cache-dir --upgrade pip uv
RUN uv sync --project /aider --no-dev
RUN git config --global --add safe.directory /aider
ENV PATH="/aider/.venv/bin:/usr/share/dotnet:${PATH}"
WORKDIR /aider
