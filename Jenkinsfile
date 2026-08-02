pipeline {
    agent any

    environment {
        K8S_NAMESPACE   = 'webstrike'
        KUBECONFIG      = '/var/lib/jenkins/.kube/config'
        INFRA_MANIFESTS = 'https://raw.githubusercontent.com/artamonovandrei/devops-diploma-infra/main/kubernetes/apps'
        BACKEND_IMAGE   = 'webstrike-backend'
        WEB_IMAGE       = 'webstrike-web'
    }

    options {
        timestamps()
        buildDiscarder(logRotator(numToKeepStr: '20'))
        timeout(time: 45, unit: 'MINUTES')
        skipDefaultCheckout(false)
        disableConcurrentBuilds(abortPrevious: true)
    }

    triggers {
        pollSCM('H/2 * * * *')
    }

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Backend tests') {
            steps {
                sh '''
                    set -e
                    python3 -m pip install --user -r backend/requirements.txt
                    export PATH="$HOME/.local/bin:$PATH"
                    cd backend
                    python3 -m pytest -q --junitxml=../test-results.xml
                '''
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'test-results.xml'
                }
            }
        }

        stage('Frontend checks') {
            steps {
                sh '''
                    set -e
                    cd frontend
                    npm ci --no-audit --no-fund || npm install --no-audit --no-fund
                    npm run typecheck
                    npm run build
                '''
            }
        }

        stage('Build images') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub-credentials', usernameVariable: 'DOCKER_USER', passwordVariable: 'DOCKER_PASS')]) {
                    sh '''
                        set -e
                        docker build -f backend/Dockerfile -t ${DOCKER_USER}/${BACKEND_IMAGE}:${BUILD_NUMBER} -t ${DOCKER_USER}/${BACKEND_IMAGE}:latest .
                        docker build -f frontend/Dockerfile -t ${DOCKER_USER}/${WEB_IMAGE}:${BUILD_NUMBER} -t ${DOCKER_USER}/${WEB_IMAGE}:latest .
                    '''
                }
            }
        }

        stage('Push to Docker Hub') {
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub-credentials', usernameVariable: 'DOCKER_USER', passwordVariable: 'DOCKER_PASS')]) {
                    sh '''
                        set -e
                        echo "$DOCKER_PASS" | docker login -u "$DOCKER_USER" --password-stdin
                        docker push ${DOCKER_USER}/${BACKEND_IMAGE}:${BUILD_NUMBER}
                        docker push ${DOCKER_USER}/${BACKEND_IMAGE}:latest
                        docker push ${DOCKER_USER}/${WEB_IMAGE}:${BUILD_NUMBER}
                        docker push ${DOCKER_USER}/${WEB_IMAGE}:latest
                    '''
                }
            }
        }

        stage('Deploy to k3s') {
            when {
                anyOf {
                    branch 'main'
                    branch 'master'
                }
            }
            steps {
                withCredentials([usernamePassword(credentialsId: 'dockerhub-credentials', usernameVariable: 'DOCKER_USER', passwordVariable: 'DOCKER_PASS')]) {
                    sh '''
                        set -e
                        export KUBECONFIG=/var/lib/jenkins/.kube/config

                        if [ -d /opt/devops-diploma-infra/.git ]; then
                          git -C /opt/devops-diploma-infra fetch --depth 1 origin main
                          git -C /opt/devops-diploma-infra reset --hard origin/main
                          kubectl apply -f /opt/devops-diploma-infra/kubernetes/apps/
                        else
                          kubectl apply -f ${INFRA_MANIFESTS}/namespace.yaml
                          kubectl apply -f ${INFRA_MANIFESTS}/backend.yaml
                          kubectl apply -f ${INFRA_MANIFESTS}/web.yaml
                        fi

                        kubectl set image deployment/backend \
                          backend=${DOCKER_USER}/${BACKEND_IMAGE}:${BUILD_NUMBER} \
                          -n ${K8S_NAMESPACE}
                        kubectl set image deployment/web \
                          web=${DOCKER_USER}/${WEB_IMAGE}:${BUILD_NUMBER} \
                          -n ${K8S_NAMESPACE}

                        kubectl rollout status deployment/backend -n ${K8S_NAMESPACE} --timeout=180s
                        kubectl rollout status deployment/web -n ${K8S_NAMESPACE} --timeout=180s
                        kubectl get pods -n ${K8S_NAMESPACE} -o wide
                    '''
                }
            }
        }
    }

    post {
        always {
            script {
                def mailSubject = "[DevOps Diploma] ${env.JOB_NAME} #${env.BUILD_NUMBER} - ${currentBuild.currentResult}"
                def mailBody = """Pipeline finished.

Job: ${env.JOB_NAME}
Build: #${env.BUILD_NUMBER}
Branch: ${env.BRANCH_NAME}
Status: ${currentBuild.currentResult}
URL: ${env.BUILD_URL}
"""
                try {
                    withCredentials([usernamePassword(credentialsId: 'ses-smtp', usernameVariable: 'SMTP_USER', passwordVariable: 'SMTP_PASS')]) {
                        sh """
                            python3 - <<'PY'
import os, smtplib, ssl
from email.mime.text import MIMEText

user = os.environ['SMTP_USER']
password = os.environ['SMTP_PASS']
from_addr = 'artamonovandrei88@gmail.com'
to_addr = 'artamonovandrei88@gmail.com'
subject = '''${mailSubject}'''
body = '''${mailBody}'''

msg = MIMEText(body, 'plain', 'utf-8')
msg['Subject'] = subject
msg['From'] = from_addr
msg['To'] = to_addr

ctx = ssl.create_default_context()
with smtplib.SMTP('email-smtp.eu-central-1.amazonaws.com', 587, timeout=30) as s:
    s.ehlo()
    s.starttls(context=ctx)
    s.ehlo()
    s.login(user, password)
    s.sendmail(from_addr, [to_addr], msg.as_string())
print('SES_EMAIL_SENT_OK')
PY
                        """
                    }
                } catch (err) {
                    echo "SES mail failed: ${err}"
                }
            }
        }
    }
}
