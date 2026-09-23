# JupyterHub sobre una máquina virtual con GPU nvidia L4 en GCP

## esta versión está dockerizada, se requiere que instale docker en la máquina, con buena CPU, Memoria y una GPU mínimo Nvidia L4.

## I. hardware requerido:

    Máquina Virtual en GCP, g2-standard-4 (4 vCPUs, 16 GB Memory), con Sistema Operativo Deep Learning Linux + GPU Nvidia L4

ESTA MÁQUINA REQUIERE AL MENOS 150 GB DE TAMAÑO EN EL DISCO DURO, SE RECOMIENDA 200 GB. SI ES LA PRIMERA VEZ QUE CREA LA VM REQUIERA DE UNA VEZ LOS 200 GB.

SI YA ESTÁ CREADA LA VM, PUEDE AGRANDAR EL DISCO DURO ASÍ:

    1. entre por la consola de gcp, a la Máquina Virtual, Ir a Storage y editar la configuración y darle EDIT en los 3 puntos (en la parte superior - derecha). Cambie a 200 GB.

    2. Entre a la máquina virtual por SSH y dele los siguientes comandos:

    df -h /
    sudo lsblk
    sudo growpart /dev/nvme0n1 1
    sudo resize2fs /dev/nvme0n1p1
    df -h /

Para crear esta VM debe solicitar incremento de quota, le llegará un email, conteste diciendo que esta VM será utilizada como parte del desarrollo de un curso de applied NLP en el marco de la MCDA, y que requiere realizar actividades de Ejecución de modelos abiertos LLM y fine-tuning, solo para fines académicos.

### acceso remoto y tunnel

Cree una clave SSH para ingresar remotamente a la máquina:

Luego en la consola de GCP:
Compute Engine -> Metadata -> SSH keys: agrege la clave pública generada

Conexión con un tunnel, para acceder al jupyter de la VM en gcp:

	ssh -i C:<ruta ClaveSSH> <usuario>@<ip de gcp> -L 8888:localhost:8888


Asi, desde un browser local, puede abrir una conexión http://localhost:8888

## II. instalar docker:

ver: https://docs.docker.com/engine/install/ubuntu/

verificar: 

    sudo systemctl status docker
    sudo systemctl enable docker
    sudo systemctl start docker

    sudo usermod -a -G docker <username_gcp>

### verificar driver nvidia para docker:

    sudo nvidia-smi

    sudo nvidia-ctk --version

    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker

    docker info | grep -i runtime
    # debe aparecer nvidia

## III. Jupyter + TensorFlow

    git clone https://github.com/AAV-99/VDI.git

    cd VDI\Ft_Rag\docker

    docker compose up -d


### IV Abra un navegador en su máquina local (DESPUES DE LANZAR EL TÚNEL), y entre a:

    localhost:8888

